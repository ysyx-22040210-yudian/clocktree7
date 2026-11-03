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
    global ct_stream ct_pending
    puts $ct_stream $record
    incr ct_pending
    if {$ct_pending >= 256} {flush $ct_stream; set ct_pending 0}
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
    global ct_trace_scope
    set net ""
    catch {set net [npi_nl_handle -type npiNlNet -refHandle $hdl]}
    set net_name [ct_prop $net npiNlFullName]
    if {$ct_trace_scope ne "" && ![ct_scope_match $net_name $ct_trace_scope]} {set net_name ""}
    return [ct_object type [ct_js [ct_prop $hdl npiNlType]] \
        name [ct_js [ct_prop $hdl npiNlFullName]] net [ct_js $net_name]]
}
proc ct_scope_match {path scope} {
    set scope [string trimright $scope ./]
    return [expr {$scope eq "" || $path eq $scope || [string first "$scope." $path] == 0 || [string first "$scope\[" $path] == 0}]
}
proc ct_inside_trace_scope {hdl} {
    global ct_trace_scope
    if {$ct_trace_scope eq ""} {return 1}
    set name [ct_prop $hdl npiNlFullName]
    if {$name ne ""} {return [ct_scope_match $name $ct_trace_scope]}
    set owner [ct_prop [ct_inst $hdl] npiNlFullName]
    return [expr {$owner ne "" && [ct_scope_match $owner $ct_trace_scope]}]
}
proc ct_scope_boundary {path endpoint direction} {
    global ct_trace_scope
    return [ct_result scope_boundary $path scope [ct_js $ct_trace_scope] endpoint [ct_js $endpoint] \
        boundary_role [ct_js [expr {$direction eq "load" ? "output" : "input"}]]]
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
    if {$path eq ""} {set path 0}
    return [ct_object status [ct_js $status] path_id $path {*}$args]
}
proc ct_journal {kind stage {context "{}"} {detail ""}} {
    global env ct_journal_stream ct_journal_sequence ct_journal_stage ct_journal_context
    if {![info exists env(CT7_WORKER_EVENTS)]} {return}
    if {$context eq ""} {set context {{}}}
    if {![info exists ct_journal_stream]} {
        set ct_journal_stream [open $env(CT7_WORKER_EVENTS) a]
        fconfigure $ct_journal_stream -encoding utf-8 -translation lf
        set ct_journal_sequence 0
    }
    incr ct_journal_sequence
    set ct_journal_stage $stage
    set ct_journal_context $context
    set record [ct_object time_ms [clock milliseconds] sequence $ct_journal_sequence pid [pid] \
        source [ct_js verdi] kind [ct_js $kind] stage [ct_js $stage] detail [ct_js $detail] context $context]
    puts $ct_journal_stream $record
    flush $ct_journal_stream
    puts "CT7_STAGE $record"
    flush stdout
}
proc ct_terminal {result} {
    global ct_trace_id ct_result_count
    ct_emit [ct_object event [ct_js trace_result] trace_id $ct_trace_id result $result]
    incr ct_result_count
}
proc ct_walk {seed direction owner pin trace_id} {
    global ct_selected ct_instances ct_max_steps ct_owner_paths ct_top ct_trace_scope ct_trace_id ct_result_count ct_stream
    set ct_trace_id $trace_id
    set trace_context [ct_object trace_id $trace_id instance [ct_js $owner] pin [ct_js $pin] direction [ct_js $direction]]
    ct_journal item_begin trace $trace_context "port trace begin"
    set ct_result_count 0
    ct_emit [ct_object event [ct_js trace_begin] trace_id $trace_id instance [ct_js $owner] pin [ct_js $pin] direction [ct_js $direction]]
    # Dictionary FIFO: remove each entry as soon as it is processed. Entries
    # carry only a parent path ID; branch prefixes are written exactly once.
    set queue [dict create 0 [list $seed 0 1 "$owner.$pin"]]
    set tail 1
    set cursor 0
    set path_count 0
    set seen [dict create]
    while {$cursor < $tail} {
        lassign [dict get $queue $cursor] hdl path is_seed last_inside
        dict unset queue $cursor
        incr cursor
        if {$ct_max_steps > 0 && $cursor > $ct_max_steps} {
            ct_terminal [ct_result trace_limit $path]
            break
        }
        if {![ct_inside_trace_scope $hdl]} {
            ct_terminal [ct_scope_boundary $path $last_inside $direction]
            continue
        }
        set state [ct_state $hdl]
        if {[dict exists $seen $state]} {continue}
        dict set seen $state 1
        set parent $path
        set path [incr path_count]
        ct_emit [ct_object event [ct_js path_step] trace_id $trace_id path_id $path parent_id $parent evidence [ct_evidence $hdl]]
        set type [ct_prop $hdl npiNlType]
        set current_name [ct_prop $hdl npiNlFullName]
        if {$current_name ne ""} {set last_inside $current_name}
        # Root interface ports are the strict boundary, including selected
        # roots. Never cross into the parent and then re-enter elsewhere.
        if {$ct_trace_scope ne "" && [string match *Port $type] &&
                [ct_prop [ct_inst $hdl] npiNlFullName] eq $ct_trace_scope} {
            set port_direction [ct_prop $hdl npiNlDirection]
            if {($direction eq "driver" && $port_direction eq "npiNlInput") ||
                    ($direction eq "load" && $port_direction eq "npiNlOutput")} {
                ct_terminal [ct_scope_boundary $path $last_inside $direction]
                continue
            }
        }
        if {!$is_seed} {
            set key [ct_key $hdl]
            if {$key ne "" && [dict exists $ct_selected $key]} {
                lassign [dict get $ct_selected $key] target target_pin role
                set want [expr {$direction eq "load" ? "input" : "output"}]
                if {$role eq $want} {
                    ct_terminal [ct_result target $path instance [ct_js $target] pin [ct_js $target_pin]]
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
                    ct_terminal [ct_result $status $path endpoint [ct_js [ct_prop $hdl npiNlFullName]]]
                    continue
                }
            }
            # Stop at non-clock pins of selected atomic structures.
            if {[dict exists $ct_instances $inst_path] && $key ne "" && ![dict exists $ct_selected $key]} {
                ct_terminal [ct_result unselected_port_boundary $path endpoint [ct_js [ct_prop $hdl npiNlFullName]] \
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
                    ct_terminal [ct_result $status $path cell [ct_js $cell] endpoint [ct_js [ct_prop $hdl npiNlFullName]]]
                    continue
                }
            }
        }
        set next {}
        if {[catch {::npi_L1::npi_nl_trace_${direction}_by_hdl2 $hdl next} message]} {
            ct_terminal [ct_result error $path message [ct_js $message]]
            continue
        }
        if {![llength $next]} {
            set status [expr {$direction eq "load" ? "no_load" : "undriven"}]
            if {[string match *Port $type] && ![string match *InstPort $type]} {
                set status [expr {$direction eq "load" ? "output_boundary" : "input_boundary"}]
            }
            if {[string match *Net $type] && [npi_nl_get -property npiNlIsLiteral -object $hdl]} {set status constant}
            if {$direction eq "driver"} {
                set attached_net ""
                catch {set attached_net [npi_nl_handle -type npiNlNet -refHandle $hdl]}
                if {$attached_net ne "" && [npi_nl_get -property npiNlIsLiteral -object $attached_net]} {set status constant}
            }
            ct_terminal [ct_result $status $path endpoint [ct_js [ct_prop $hdl npiNlFullName]]]
        }
        foreach target $next {dict set queue $tail [list $target $path 0 $last_inside]; incr tail}
        unset next
    }
    if {!$ct_result_count} {ct_terminal [ct_result error 0 message [ct_js "No terminal or target found; possible connectivity cycle"]]}
    ct_emit [ct_object event [ct_js trace_end] trace_id $trace_id result_count $ct_result_count path_steps $path_count]
    flush $ct_stream
    ct_journal item_end trace $trace_context "port trace complete"
    return $ct_result_count
}
proc ct_atomic_json {path record {backup 0}} {
    set temporary "$path.tmp"
    set stream [open $temporary w]
    fconfigure $stream -encoding utf-8 -translation lf
    puts $stream $record
    close $stream
    if {$backup && [file exists $path]} {file copy -force $path "$path.previous"}
    file rename -force $temporary $path
}
proc ct_worker_status {stage {unit ""}} {
    global ct_context ct_status_file
    if {$ct_context} {ct_atomic_json $ct_status_file [ct_object stage [ct_js $stage] unit [ct_js $unit]]}
}
proc ct_save_checkpoint {stage queries traces} {
    global ct_context ct_checkpoint ct_signature ct_stream ct_target_stream ct_instances
    global ct_completed_modules ct_last_module ct_saved_module ct_module_checkpoint_dir
    if {!$ct_context} {return}
    flush $ct_stream
    flush $ct_target_stream
    set record [ct_object version 1 signature [ct_js $ct_signature] stage [ct_js $stage] \
        completed_queries $queries completed_traces $traces instances [dict size $ct_instances] \
        completed_modules $ct_completed_modules last_module $ct_last_module \
        raw_offset [tell $ct_stream] targets_offset [tell $ct_target_stream]]
    if {$stage eq "enumerate" && $ct_completed_modules > $ct_saved_module} {
        # The candidate snapshot is published before the authoritative cursor.
        # Recovery discards snapshots beyond that cursor if the worker dies
        # between these two atomic replacements.
        ct_atomic_json [file join $ct_module_checkpoint_dir [format %08d.json $ct_completed_modules]] $record
        set ct_saved_module $ct_completed_modules
    }
    ct_atomic_json $ct_checkpoint $record 1
}
proc ct_module_memory_check {} {
    global ct_context ct_module_check_enabled ct_module_check_request ct_module_check_ack ct_completed_modules ct_controller_pid
    if {!$ct_context || !$ct_module_check_enabled} {return}
    set temporary "$ct_module_check_request.tmp"
    set stream [open $temporary w]
    puts $stream $ct_completed_modules
    close $stream
    file rename -force $temporary $ct_module_check_request
    # This is a boundary handshake, not a runtime timeout. Do not enter the
    # next module until the supervisor has sampled actual RSS at this point.
    while {1} {
        if {[file exists $ct_module_check_ack] && ![catch {set stream [open $ct_module_check_ack r]}]} {
            set reply [read $stream]; close $stream
            if {[llength $reply] == 3 && [lindex $reply 0] eq "$ct_completed_modules"} {
                if {[lindex $reply 1] ni {continue rotate} || ![string is wideinteger -strict [lindex $reply 2]]} {
                    error "Invalid module memory check response"
                }
                return
            }
        }
        if {![file exists /proc/$ct_controller_pid]} {error "Extraction controller exited during module checkpoint check"}
        after 20
    }
}
proc ct_commit_module {queries query rule layer scope module matched added status} {
    global ct_completed_modules ct_last_module ct_total_modules
    incr ct_completed_modules
    set ct_last_module [ct_object index $ct_completed_modules query_index $query rule_in_query $rule \
        layer $layer scope [ct_js $scope] module [ct_js $module] matched_instances $matched \
        new_instances $added status [ct_js $status]]
    ct_emit [ct_object event [ct_js module_checkpoint] checkpoint $ct_last_module]
    ct_save_checkpoint enumerate $queries 0
    ct_journal checkpoint modules $ct_last_module "module checkpoint committed"
    puts "CT7_MODULE_CHECKPOINT index=$ct_completed_modules/$ct_total_modules query=$query rule=$rule layer=$layer module=$module scope=$scope matched=$matched status=$status"
    flush stdout
    ct_worker_status module_checkpoint "module $ct_completed_modules/$ct_total_modules L$layer $scope / $module"
    ct_module_memory_check
    return [ct_maybe_rotate enumerate $queries 0]
}
proc ct_close_streams {} {
    global ct_stream ct_target_stream
    catch {close $ct_stream}
    catch {close $ct_target_stream}
}
proc ct_maybe_rotate {stage queries traces} {
    global ct_context ct_rotate_file
    if {!$ct_context || ![file exists $ct_rotate_file]} {return 0}
    ct_save_checkpoint $stage $queries $traces
    ct_worker_status rotating "$queries module queries / $traces traces committed"
    ct_close_streams
    puts "CT7_CHECKPOINT_ROTATE queries=$queries traces=$traces"
    flush stdout
    return 1
}
proc ct_register_instance {path module layer inputs output kind source emit} {
    global ct_instances ct_selected ct_owner_paths ct_pins_to_trace ct_pin_count ct_context ct_target_stream
    set register_stage [expr {$emit ? "modules" : "restore"}]
    set register_context [ct_object instance [ct_js $path] module [ct_js $module] layer $layer]
    ct_journal item_begin $register_stage $register_context "bind instance ports"
    dict set ct_instances $path $layer
    if {$emit} {
        ct_emit [ct_object event [ct_js instance] path [ct_js $path] module [ct_js $module] layer $layer \
            inputs [ct_strings $inputs] output [ct_js $output] kind [ct_js $kind] source [ct_js $source]]
        if {$ct_context} {puts $ct_target_stream [list ct_target $path $module $layer $inputs $output $kind $source]}
    }
    foreach pin [concat $inputs [list $output]] {
        set role [expr {$pin eq $output ? "output" : "input"}]
        set direction [expr {$role eq "output" ? "load" : "driver"}]
        set p ""
        catch {set p [ct_pin $path $pin]}
        set status ""
        if {$p eq ""} {set status missing_pin} elseif {[npi_nl_get -property npiNlSize -object $p] != 1} {set status width_requires_bit}
        if {$p ne "" && ![ct_inside_trace_scope $p]} {
            set status error
            if {$emit} {ct_emit [ct_object event [ct_js diagnostic] code [ct_js pin_validation] instance [ct_js $path] pin [ct_js $pin] \
                message [ct_js "Resolved seed port is outside the configured trace root"]]}
        }
        if {$status ne ""} {
            if {$emit} {
                ct_emit [ct_object event [ct_js trace] instance [ct_js $path] pin [ct_js $pin] direction [ct_js $direction] \
                    results [ct_array [list [ct_object status [ct_js $status] path {[]}]]]]
            }
            continue
        }
        set expected [expr {$role eq "output" ? "npiNlOutput" : "npiNlInput"}]
        if {$emit && [ct_prop $p npiNlDirection] ne $expected} {
            ct_emit [ct_object event [ct_js diagnostic] code [ct_js pin_validation] instance [ct_js $path] pin [ct_js $pin]]
        }
        dict set ct_selected [ct_key $p] [list $path $pin $role]
        dict set ct_owner_paths [ct_prop [ct_inst $p] npiNlFullName] $path
        dict set ct_pins_to_trace $ct_pin_count [list $p $direction $path $pin]
        incr ct_pin_count
    }
    ct_journal item_end $register_stage $register_context "instance ports bound"
}
proc ct_restore_targets {} {
    global ct_target_file ct_resume_instances ct_instances
    if {![file exists $ct_target_file]} {return}
    set input [open $ct_target_file r]
    fconfigure $input -encoding utf-8 -translation lf
    set record ""
    while {[gets $input line] >= 0} {
        append record $line \n
        if {![info complete $record]} {continue}
        # Parse a list of data, never source/evaluate a checkpoint as a script.
        if {[llength $record] != 8 || [lindex $record 0] ne "ct_target"} {error "Invalid target checkpoint record"}
        ct_register_instance {*}[lrange $record 1 end] 0
        set record ""
    }
    close $input
    if {[string trim $record] ne "" || [dict size $ct_instances] != $ct_resume_instances} {
        error "Target checkpoint is incomplete"
    }
}
proc ct_main {} {
    global env ct_output ct_rules ct_stream ct_selected ct_instances ct_elab ct_filelist ct_top ct_max_steps ct_owner_paths ct_tree_type ct_trace_scope ct_pending
    global ct_context ct_target_file ct_target_stream ct_resume_completed_queries ct_resume_completed_traces ct_resume_instances ct_resume_stage ct_pins_to_trace ct_pin_count
    global ct_resume_completed_modules ct_resume_last_module ct_completed_modules ct_last_module ct_saved_module ct_total_modules
    ct_journal begin worker {} "read worker configuration"
    source $env(CT7_CONFIG)
    set ct_context [info exists env(CT7_RESUME)]
    if {$ct_context} {
        # The supervisor generates this small context from validated numbers.
        uplevel #0 [list source $env(CT7_RESUME)]
    } else {
        set ct_resume_completed_queries 0
        set ct_resume_completed_traces 0
        set ct_resume_instances 0
        set ct_resume_stage import
        set ct_resume_completed_modules 0
        set ct_resume_last_module null
    }
    if {![info exists ct_tree_type]} {set ct_tree_type clock}
    if {![info exists ct_trace_scope]} {set ct_trace_scope ""}
    set ct_selected [dict create]
    set ct_instances [dict create]
    set ct_owner_paths [dict create]
    set ct_pins_to_trace [dict create]
    set ct_pin_count 0
    set ct_completed_modules $ct_resume_completed_modules
    set ct_last_module $ct_resume_last_module
    set ct_saved_module $ct_resume_completed_modules
    set ct_total_modules [llength $ct_rules]
    set ct_stream [open $ct_output [expr {$ct_context ? "a" : "w"}]]
    set ct_pending 0
    fconfigure $ct_stream -encoding utf-8 -translation lf
    seek $ct_stream 0 end
    if {$ct_context} {
        set ct_target_stream [open $ct_target_file a]
        fconfigure $ct_target_stream -encoding utf-8 -translation lf
        seek $ct_target_stream 0 end
    }
    if {[tell $ct_stream] == 0} {
        ct_emit [ct_object event [ct_js metadata] backend [ct_js verdi_base_npi] \
            verdi_home [ct_js $env(VERDI_HOME)] top [ct_js $ct_top] tree_type [ct_js $ct_tree_type] trace_scope [ct_js $ct_trace_scope] raw_protocol 2 module_checkpoint_protocol 1]
        flush $ct_stream
        ct_save_checkpoint import 0 0
    }
    ct_worker_status import
    ct_journal end worker {} "worker configuration ready"
    ct_journal begin import [ct_object top [ct_js $ct_top]] "import elab / RTL"
    source $env(VERDI_HOME)/share/NPI/L1/TCL/npi_L1.tcl
    schSetPreference -detailRTL on -detailMux on -recogFSM off -expandGenBlock on
    puts "CT7_IMPORT_BEGIN"
    flush stdout
    if {[info exists ct_elab] && $ct_elab ne ""} {
        debImport -elab $ct_elab
    } else {
        debImport -sv -f $ct_filelist -top $ct_top
    }
    puts "CT7_IMPORT_END"
    flush stdout
    if {[file exists verdiLog/compiler.log]} {
        set log_file [open verdiLog/compiler.log r]
        set error_count 0
        while {[gets $log_file line] >= 0} {incr error_count [regexp -all {\*Error\*|Error-\[} $line]}
        close $log_file
        if {$error_count > 0} {error "HDL elaboration has $error_count compiler errors; use a valid elab/KDB"}
    }
    set top_hdl [npi_handle_by_name -name $ct_top -scope ""]
    if {$top_hdl eq ""} {error "Requested top was not elaborated: $ct_top"}
    if {$ct_trace_scope ne "" && [npi_handle_by_name -name $ct_trace_scope -scope ""] eq ""} {
        ct_emit [ct_object event [ct_js diagnostic] code [ct_js scope_not_found] hierarchy [ct_js $ct_trace_scope]]
        error "Port trace root was not elaborated: $ct_trace_scope"
    }
    ct_journal end import [ct_object top [ct_js $ct_top]] "elaboration imported and validated"
    if {$ct_context} {
        ct_worker_status restore_targets
        ct_journal begin restore [ct_object instances $ct_resume_instances modules $ct_resume_completed_modules traces $ct_resume_completed_traces] "restore committed targets"
        ct_restore_targets
        ct_journal end restore [ct_object instances [dict size $ct_instances]] "committed target handles restored"
    }
    puts "CT7_RESUME queries=$ct_resume_completed_queries traces=$ct_resume_completed_traces instances=[dict size $ct_instances] modules=$ct_resume_completed_modules"
    flush stdout
    set query_groups [dict create]
    foreach rule $ct_rules {
        lassign $rule layer scope module inputs output kind
        set scope [string trimright [string trim $scope] ./]
        if {$scope eq ""} {set scope [expr {$ct_trace_scope ne "" ? $ct_trace_scope : $ct_top}]}
        if {$ct_trace_scope ne "" && ![ct_scope_match $scope $ct_trace_scope]} {error "Instance search scope is outside port trace root: $scope"}
        dict lappend query_groups [list $scope $module] [lreplace $rule 1 1 $scope]
    }
    # Never widen a query to a common ancestor: share only identical scope /
    # module queries. This preserves strict hierarchy bounds and conflicts.
    set query_count 0
    set module_cursor 0
    ct_journal begin modules [ct_object completed_modules $ct_completed_modules total_modules $ct_total_modules] "enumerate configured module rules"
    dict for {query rules} $query_groups {
        incr query_count
        set group_end [expr {$module_cursor + [llength $rules]}]
        if {$group_end <= $ct_resume_completed_modules} {
            set module_cursor $group_end
            puts "CT7_MODULE_SKIP query=$query_count completed_modules=$module_cursor"
            flush stdout
            continue
        }
        lassign $query scope module
        ct_worker_status enumerate "$scope / $module"
        set scope_found [expr {[npi_handle_by_name -name $scope -scope ""] ne ""}]
        set handles {}
        if {$scope_found} {
            puts "CT7_ENUM_BEGIN scope=$scope module=$module rules=[llength $rules]"
            flush stdout
            ct_journal item_begin modules [ct_object query_index $query_count scope [ct_js $scope] module [ct_js $module]] "native instance query"
            ::npi_L1::npi_find_inst_with_def_wildcard $scope $module handles
            ct_journal item_end modules [ct_object query_index $query_count scope [ct_js $scope] module [ct_js $module] candidates [llength $handles]] "native query returned"
            ct_emit [ct_object event [ct_js scoped_query] query_index $query_count scope [ct_js $scope] module [ct_js $module] \
                rules [llength $rules] candidates [llength $handles] resumed_rules [expr {max(0,$ct_resume_completed_modules-$module_cursor)}]]
        }
        set rule_index 0
        foreach rule $rules {
        incr module_cursor
        incr rule_index
        if {$module_cursor <= $ct_resume_completed_modules} {
            puts "CT7_MODULE_SKIP index=$module_cursor query=$query_count rule=$rule_index"
            flush stdout
            continue
        }
        lassign $rule layer scope module inputs output kind
        ct_journal item_begin modules [ct_object index $module_cursor query_index $query_count rule_in_query $rule_index \
            layer $layer scope [ct_js $scope] module [ct_js $module]] "apply module rule"
        set matched 0
        set before [dict size $ct_instances]
        set status ok
        if {!$scope_found} {
            set status scope_not_found
            ct_emit [ct_object event [ct_js diagnostic] code [ct_js scope_not_found] hierarchy [ct_js $scope] layer $layer]
        } else {
          foreach hdl $handles {
            set path [npi_get_str -property npiFullName -object $hdl]
            if {![ct_scope_match $path $scope]} {continue}
            if {$ct_trace_scope ne "" && ![ct_scope_match $path $ct_trace_scope]} {error "NPI returned target outside port trace root: $path"}
            incr matched
            if {[dict exists $ct_instances $path]} {
                if {[dict get $ct_instances $path] != $layer} {
                    set status layer_conflict
                    ct_emit [ct_object event [ct_js diagnostic] code [ct_js layer_conflict] instance [ct_js $path]]
                }
                continue
            }
            ct_register_instance $path $module $layer $inputs $output $kind [::npi_L1::npi_ut_get_hdl_info $hdl] 1
          }
        }
        if {$scope_found && !$matched} {
            set status missing_module
            ct_emit [ct_object event [ct_js diagnostic] code [ct_js missing_module] module [ct_js $module] hierarchy [ct_js $scope] layer $layer]
        }
        puts "CT7_ENUM layer=$layer module=$module instances=$matched"
        flush stdout
        set committed_queries [expr {$rule_index == [llength $rules] ? $query_count : $query_count-1}]
        if {$rule_index == [llength $rules]} {unset handles}
        if {[ct_commit_module $committed_queries $query_count $rule_index $layer $scope $module $matched \
                [expr {[dict size $ct_instances]-$before}] $status]} {return}
        }
    }
    if {$ct_completed_modules != $ct_total_modules} {error "Module checkpoint count does not cover all configured rules"}
    ct_journal end modules [ct_object completed_modules $ct_completed_modules instances [dict size $ct_instances]] "all module rules committed"
    unset query_groups ct_rules
    ct_save_checkpoint trace $query_count $ct_resume_completed_traces
    set count $ct_resume_completed_traces
    if {$count > $ct_pin_count} {error "Checkpoint trace count exceeds restored ports"}
    for {set index 0} {$index < $count} {incr index} {dict unset ct_pins_to_trace $index}
    ct_worker_status trace "$count / $ct_pin_count"
    ct_journal begin trace [ct_object completed_traces $count total_traces $ct_pin_count] "trace all selected ports"
    while {$count < $ct_pin_count} {
        lassign [dict get $ct_pins_to_trace $count] p direction owner pin
        dict unset ct_pins_to_trace $count
        incr count
        ct_walk $p $direction $owner $pin $count
        if {$count % 32 == 0} {ct_save_checkpoint trace $query_count $count}
        if {[ct_maybe_rotate trace $query_count $count]} {return}
        if {$count % 100 == 0} {
            puts "CT7_TRACE $count/$ct_pin_count"; flush stdout
            ct_worker_status trace "$count / $ct_pin_count"
        }
    }
    ct_emit [ct_object event [ct_js complete] instances [dict size $ct_instances] pin_traces $count module_rules $ct_completed_modules]
    ct_save_checkpoint complete $query_count $count
    ct_worker_status complete
    ct_journal end trace [ct_object completed_traces $count total_traces $ct_pin_count] "all selected ports traced"
    ct_close_streams
    puts "CT7_COMPLETE instances=[dict size $ct_instances] traces=$count"
}
if {[catch {ct_main} message]} {
    puts stderr "CT7_FATAL: $message\n$::errorInfo"
    catch {ct_journal failed $ct_journal_stage [ct_object operation $ct_journal_context error [ct_js [string range $message 0 4095]]] "Verdi extraction error"}
    if {[info exists ct_stream]} {
        catch {ct_emit [ct_object event [ct_js diagnostic] code [ct_js fatal] message [ct_js $message]]}
        catch {close $ct_stream}
    }
    catch {ct_worker_status failed $message}
    catch {ct_close_streams}
}
debExit
