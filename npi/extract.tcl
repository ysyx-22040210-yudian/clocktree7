# Uses supported base NPI driver/load interfaces, not the Universe trace wrapper.
proc ct_js {value} {
    return "\"[string map [list \\ \\\\ \" \\\" \n \\n \r \\r \t \\t] $value]\""
}
proc ct_array {values} { return "\[[join $values ,]\]" }
proc ct_strings {values} {
    set result {}
    foreach value $values {lappend result [ct_js $value]}
    return [ct_array $result]
}
proc ct_object {args} {
    set fields {}
    foreach {key value} $args {lappend fields "[ct_js $key]:$value"}
    return "\{[join $fields ,]\}"
}
proc ct_emit {record} {
    global ct_stream
    puts $ct_stream $record
    flush $ct_stream
}
proc ct_prop {hdl property} {
    if {$hdl eq ""} {return ""}
    return [npi_nl_get_str -property $property -object $hdl]
}
proc ct_full {hdl} {
    set full $hdl
    if {[string match *Pseudo* [ct_prop $hdl npiNlType]]} {
        set parent [npi_nl_handle -type npiNlParent -refHandle $hdl]
        if {$parent ne ""} {set full $parent}
    }
    return $full
}
proc ct_inst {hdl} {return [npi_nl_handle -type npiNlInst -refHandle [ct_full $hdl]]}
proc ct_key {hdl} {
    set inst [ct_inst $hdl]
    if {$inst eq ""} {return ""}
    set full [ct_full $hdl]
    return [list [ct_prop $inst npiNlFullName] [ct_prop $full npiNlName] \
        [npi_nl_get -property npiNlLeft -object $hdl] [npi_nl_get -property npiNlRight -object $hdl]]
}
proc ct_state {hdl} {
    return [list [ct_prop $hdl npiNlType] [ct_prop $hdl npiNlFullName] \
        [npi_nl_get -property npiNlLeft -object $hdl] [npi_nl_get -property npiNlRight -object $hdl]]
}
proc ct_evidence {hdl} {
    set net ""
    catch {set net [npi_nl_handle -type npiNlNet -refHandle $hdl]}
    return [ct_object type [ct_js [ct_prop $hdl npiNlType]] \
        name [ct_js [ct_prop $hdl npiNlFullName]] net [ct_js [ct_prop $net npiNlFullName]]]
}
proc ct_scope_match {path scope} {
    set scope [string trimright $scope ./]
    return [expr {$scope eq "" || $path eq $scope || [string first "$scope." $path] == 0 || [string first "$scope\[" $path] == 0}]
}
proc ct_pin {path pin} {
    set base $pin
    set bit ""
    regexp {^(.*)\[([0-9]+)\]$} $pin -> base bit
    set split [string last . $path]
    set result ""
    if {$split >= 0} {
        set scope [string range $path 0 [expr {$split-1}]]
        set inst [string range $path [expr {$split+1}] end]
        catch {set result [::npi_L1::npi_nl_instport_handle_by_nl_name $scope $inst $base]}
    }
    # A generate scope is not a netlist module scope. Resolve its declared
    # signal and take the boundary port instead of splitting its HDL name.
    if {$result eq ""} {
        set sig ""
        catch {set sig [::npi_L1::npi_nl_sig_handle_by_name "$path.$base"]}
        if {$sig ne ""} {
            foreach direction {driver load} {
                set connections {}
                ::npi_L1::npi_nl_trace_${direction}_by_hdl2 $sig connections
                foreach p $connections {
                    set kind [ct_prop $p npiNlType]
                    if {$kind in {npiNlPort npiNlPseudoPort}} {
                        set port [npi_nl_handle -type npiNlInstPort -refHandle $p]
                        if {$port ne ""} {set result $port; break}
                    }
                }
                if {$result ne ""} {break}
            }
        }
    }
    if {$result ne "" && $bit ne ""} {
        set result [npi_nl_handle_by_index -object $result -index $bit]
    }
    return $result
}
proc ct_result {status path args} {
    return [ct_object status [ct_js $status] path [ct_array $path] {*}$args]
}
proc ct_walk {seed direction owner pin} {
    global ct_selected ct_instances ct_max_steps ct_owner_paths ct_top
    set queue [list [list $seed {} 1]]
    set cursor 0
    set seen [dict create]
    set results {}
    while {$cursor < [llength $queue]} {
        lassign [lindex $queue $cursor] hdl path is_seed
        incr cursor
        if {$cursor > $ct_max_steps} {
            lappend results [ct_result trace_limit $path]
            break
        }
        set state [ct_state $hdl]
        if {[dict exists $seen $state]} {continue}
        dict set seen $state 1
        lappend path [ct_evidence $hdl]
        set type [ct_prop $hdl npiNlType]
        if {!$is_seed} {
            set key [ct_key $hdl]
            if {$key ne "" && [dict exists $ct_selected $key]} {
                lassign [dict get $ct_selected $key] target target_pin role
                set want [expr {$direction eq "load" ? "input" : "output"}]
                if {$role eq $want} {
                    lappend results [ct_result target $path instance [ct_js $target] pin [ct_js $target_pin]]
                    continue
                }
            }
            set inst [ct_inst $hdl]
            set inst_path [ct_prop $inst npiNlFullName]
            if {[dict exists $ct_owner_paths $inst_path]} {set inst_path [dict get $ct_owner_paths $inst_path]}
            set cell [ct_prop $inst npiNlCellType]
            if {$inst_path eq $ct_top && $type in {npiNlPort npiNlPseudoPort}} {
                set port_direction [ct_prop $hdl npiNlDirection]
                if {($direction eq "load" && $port_direction eq "npiNlOutput") || ($direction eq "driver" && $port_direction eq "npiNlInput")} {
                    set status [expr {$direction eq "load" ? "output_boundary" : "input_boundary"}]
                    lappend results [ct_result $status $path endpoint [ct_js [ct_prop $hdl npiNlFullName]]]
                    continue
                }
            }
            # Stop at non-clock pins of selected atomic structures.
            if {[dict exists $ct_instances $inst_path] && $key ne "" && ![dict exists $ct_selected $key]} {
                lappend results [ct_result unselected_port_boundary $path endpoint [ct_js [ct_prop $hdl npiNlFullName]] \
                    pin_direction [ct_js [ct_prop $hdl npiNlDirection]]]
                continue
            }
            if {[string match *InstPort $type] && $cell ne "npiNlModuleCell"} {
                set is_interface_connector 0
                if {$cell eq "npiNlMacroCell"} {
                    catch {set is_interface_connector [npi_nl_get -property npiNlIsIntfConnector -object $inst]}
                }
                if {!$is_interface_connector && $cell ni {npiNlAssignCell npiNlBufCell npiNlNegCell}} {
                    set status unknown_cell
                    if {$direction eq "load" && $cell in {npiNlFlipFlopCell npiNlLatchCell npiNlFSMCell npiNlExternalRamCell npiNlFlipFlopComboCell npiNlLatchComboCell}} {set status sink}
                    lappend results [ct_result $status $path cell [ct_js $cell] endpoint [ct_js [ct_prop $hdl npiNlFullName]]]
                    continue
                }
            }
        }
        set next {}
        if {[catch {::npi_L1::npi_nl_trace_${direction}_by_hdl2 $hdl next} message]} {
            lappend results [ct_result error $path message [ct_js $message]]
            continue
        }
        if {![llength $next]} {
            set status [expr {$direction eq "load" ? "no_load" : "undriven"}]
            if {[string match *Port $type] && ![string match *InstPort $type]} {
                set status [expr {$direction eq "load" ? "output_boundary" : "input_boundary"}]
            }
            if {[string match *Net $type] && [npi_nl_get -property npiNlIsLiteral -object $hdl]} {set status constant}
            lappend results [ct_result $status $path endpoint [ct_js [ct_prop $hdl npiNlFullName]]]
        }
        foreach target $next {lappend queue [list $target $path 0]}
    }
    if {![llength $results]} {lappend results [ct_result error {} message [ct_js "No terminal or target found; possible connectivity cycle"]]}
    return $results
}
proc ct_main {} {
    global env ct_output ct_rules ct_stream ct_selected ct_instances ct_elab ct_filelist ct_top ct_max_steps ct_owner_paths
    source $env(CT7_CONFIG)
    set ct_stream [open $ct_output w]
    fconfigure $ct_stream -encoding utf-8
    ct_emit [ct_object event [ct_js metadata] backend [ct_js verdi_base_npi] \
        verdi_home [ct_js $env(VERDI_HOME)] top [ct_js $ct_top]]
    source $env(VERDI_HOME)/share/NPI/L1/TCL/npi_L1.tcl
    schSetPreference -detailRTL on -detailMux on -recogFSM off -expandGenBlock on
    if {[info exists ct_elab] && $ct_elab ne ""} {
        debImport -elab $ct_elab
    } else {
        debImport -sv -f $ct_filelist -top $ct_top
    }
    if {[file exists verdiLog/compiler.log]} {
        set log_file [open verdiLog/compiler.log r]
        set log_text [read $log_file]
        close $log_file
        set error_count [regexp -all {\*Error\*|Error-\[} $log_text]
        if {$error_count > 0} {error "HDL elaboration has $error_count compiler errors; use a valid elab/KDB"}
    }
    set top_hdl [npi_handle_by_name -name $ct_top -scope ""]
    if {$top_hdl eq ""} {error "Requested top was not elaborated: $ct_top"}
    set design_handles {}
    ::npi_L1::npi_find_inst_wildcard "" * design_handles
    ct_emit [ct_object event [ct_js metadata] design_instances [llength $design_handles]]
    puts "CT7_DESIGN instances=[llength $design_handles]"
    set ct_selected [dict create]
    set ct_instances [dict create]
    set ct_owner_paths [dict create]
    set pins_to_trace {}
    foreach rule $ct_rules {
        lassign $rule layer scope module inputs output kind
        set handles {}
        ::npi_L1::npi_find_inst_with_def_wildcard "" $module handles
        set matched 0
        foreach hdl $handles {
            set path [npi_get_str -property npiFullName -object $hdl]
            if {![ct_scope_match $path $scope]} {continue}
            incr matched
            if {[dict exists $ct_instances $path]} {
                if {[dict get $ct_instances $path] != $layer} {
                    ct_emit [ct_object event [ct_js diagnostic] code [ct_js layer_conflict] instance [ct_js $path]]
                }
                continue
            }
            dict set ct_instances $path $layer
            ct_emit [ct_object event [ct_js instance] path [ct_js $path] module [ct_js $module] layer $layer \
                inputs [ct_strings $inputs] output [ct_js $output] kind [ct_js $kind] source [ct_js [::npi_L1::npi_ut_get_hdl_info $hdl]]]
            foreach pin [concat $inputs [list $output]] {
                set role [expr {$pin eq $output ? "output" : "input"}]
                set direction [expr {$role eq "output" ? "load" : "driver"}]
                set p ""
                catch {set p [ct_pin $path $pin]}
                set status ""
                if {$p eq ""} {set status missing_pin} elseif {[npi_nl_get -property npiNlSize -object $p] != 1} {set status width_requires_bit}
                if {$status ne ""} {
                    ct_emit [ct_object event [ct_js trace] instance [ct_js $path] pin [ct_js $pin] direction [ct_js $direction] \
                        results [ct_array [list [ct_result $status {}]]]]
                    continue
                }
                set expected [expr {$role eq "output" ? "npiNlOutput" : "npiNlInput"}]
                if {[ct_prop $p npiNlDirection] ne $expected} {
                    ct_emit [ct_object event [ct_js diagnostic] code [ct_js pin_validation] instance [ct_js $path] pin [ct_js $pin]]
                }
                dict set ct_selected [ct_key $p] [list $path $pin $role]
                dict set ct_owner_paths [ct_prop [ct_inst $p] npiNlFullName] $path
                lappend pins_to_trace [list $p $direction $path $pin]
            }
        }
        if {!$matched} {
            ct_emit [ct_object event [ct_js diagnostic] code [ct_js missing_module] module [ct_js $module] hierarchy [ct_js $scope] layer $layer]
        }
        puts "CT7_ENUM layer=$layer module=$module instances=$matched"
        flush stdout
    }
    set count 0
    foreach item $pins_to_trace {
        lassign $item p direction owner pin
        set results [ct_walk $p $direction $owner $pin]
        ct_emit [ct_object event [ct_js trace] instance [ct_js $owner] pin [ct_js $pin] direction [ct_js $direction] results [ct_array $results]]
        incr count
        if {$count % 100 == 0} {puts "CT7_TRACE $count/[llength $pins_to_trace]"; flush stdout}
    }
    ct_emit [ct_object event [ct_js complete] instances [dict size $ct_instances] pin_traces $count]
    close $ct_stream
    puts "CT7_COMPLETE instances=[dict size $ct_instances] traces=$count"
}
if {[catch {ct_main} message]} {
    puts stderr "CT7_FATAL: $message\n$::errorInfo"
    if {[info exists ct_stream]} {
        catch {ct_emit [ct_object event [ct_js diagnostic] code [ct_js fatal] message [ct_js $message]]}
        catch {close $ct_stream}
    }
}
debExit
