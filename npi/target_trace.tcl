# Language Model target-structure extraction. No Netlist Model/load calls.
# Persistent indexes contain names/data only: native handles never cross a
# port checkpoint or process lifetime. Public handles are reclaimed per port.
proc ct_l_prop {h p} {
    if {$h eq ""} {return ""}
    return [npi_get_str -property $p -object $h]
}
proc ct_l_size {h} {return [npi_get -property npiSize -object $h]}
proc ct_l_key {h} {
    set name [ct_l_prop $h npiFullName]
    if {$name eq ""} {return ""}
    if {[ct_l_prop $h npiType] in {npiBitSelect npiIndexedPartSelect npiRefObj}} {return ""}
    # A symbolic index can be evaluated in different generate instances.
    # It may be traced, but must not share a cache entry by text alone.
    if {[regexp {\[[^]]*[^0-9:+ -][^]]*\]} $name]} {return ""}
    return [list $name [ct_l_size $h]]
}
proc ct_l_ev {h {name ""}} {
    if {$name eq ""} {set name [ct_l_prop $h npiFullName]}
    return [ct_object type [ct_js [ct_l_prop $h npiType]] name [ct_js $name] net [ct_js ""]]
}
proc ct_l_port {path pin} {
    set base $pin; set bit ""
    regexp {^(.*)\[(-?[0-9]+)\]$} $pin -> base bit
    set inst [npi_handle_by_name -name $path -scope ""]
    set result [dict create status missing_pin low "" high "" port ""]
    if {$inst eq ""} {return $result}
    set it [npi_iterate -type npiPort -refHandle $inst]
    if {$it eq ""} {npi_release_handle -object $inst; return $result}
    while {[set p [npi_scan -iterator $it]] ne ""} {
        if {[ct_l_prop $p npiName] ne $base} {npi_release_handle -object $p; continue}
        set low [npi_handle -type npiLowConn -refHandle $p]
        set high [npi_handle -type npiHighConn -refHandle $p]
        if {$low ne "" && $bit ne ""} {
            set low [npi_handle_by_index -object $low -index $bit]
            # A whole actual bus is not the selected formal bit. Start from
            # its low connection so the native language API maps the bit.
            set high ""
        }
        set status ok
        if {$low eq ""} {set status missing_pin} elseif {[ct_l_size $low] != 1} {set status width_requires_bit}
        set result [dict create status $status low $low high $high port $p direction [ct_l_prop $p npiDirection]]
    }
    npi_release_handle -object $inst
    return $result
}
proc ct_l_free_port {data} {
    foreach key {low high port} {
        set h [dict get $data $key]
        if {$h ne ""} {npi_release_handle -object $h}
    }
}
proc ct_l_init {} {
    set ::ct_l_outputs [dict create]
    set ::ct_l_output_rules [dict create]
    set ::ct_l_cache [dict create]
    set ::ct_l_cache_bytes 0
    set ::ct_l_cache_written 0
    set ::ct_l_totals [dict create driver_queries 0 cache_hits 0 output_index_hits 0]
    set ::ct_l_root [expr {$::ct_trace_scope ne "" ? $::ct_trace_scope : $::ct_top}]
}
proc ct_l_register {path inputs output emit} {
    global ct_pins_to_trace ct_pin_count ct_l_outputs ct_l_output_rules
    set pins $inputs
    if {$output ne ""} {
        dict set ct_l_output_rules $path $output
        lappend pins $output
    }
    foreach pin $pins {
        set role [expr {$pin eq $output ? "output" : "input"}]
        set data [ct_l_port $path $pin]
        set status [dict get $data status]
        if {$status eq "ok" && [dict get $data direction] ne [expr {$role eq "output" ? "npiOutput" : "npiInput"}]} {set status error}
        if {$role eq "output" && $status eq "ok"} {
            set actual [npi_handle -type npiHighConn -refHandle [dict get $data port]]
            if {$actual ne ""} {
                if {![ct_l_transparent $actual]} {set status unsupported_expression}
                npi_release_handle -object $actual
            }
        }
        if {$role eq "output"} {
            if {$emit} {ct_emit [ct_object event [ct_js output_check] instance [ct_js $path] pin [ct_js $pin] status [ct_js $status]]}
            if {$status eq "ok"} {
                foreach side {low high} {
                    set h [dict get $data $side]
                    if {$h eq "" || [ct_l_size $h] != 1} {continue}
                    set key [ct_l_key $h]
                    if {$key ne ""} {dict lappend ct_l_outputs $key [list $path $pin $side]}
                }
            }
        } elseif {$status ne "ok"} {
            if {$emit} {ct_emit [ct_object event [ct_js trace] instance [ct_js $path] pin [ct_js $pin] direction [ct_js driver] \
                results [ct_array [list [ct_object status [ct_js $status] path {[]}]]]]}
        } else {
            # A stable descriptor, not a retained NPI handle.
            dict set ct_pins_to_trace $ct_pin_count [list "" driver $path $pin]
            incr ct_pin_count
        }
        ct_l_free_port $data
    }
}
proc ct_l_constant_expr {h {seen {}}} {
    if {$h eq "" || [dict exists $seen $h]} {return 0}
    dict set seen $h 1
    set type [ct_l_prop $h npiType]
    if {$type in {npiConstant npiParameter npiParameterBit npiGenVar}} {return 1}
    if {$type in {npiExtRef npiRefObj}} {
        # Imported package parameters appear as ExtRef in Verdi 2018.
        # Follow the native binding; references to runtime variables stay false.
        return [ct_l_constant_expr [npi_handle -type npiActual -refHandle $h] $seen]
    }
    if {$type ne "npiOperation"} {return 0}
    set it [npi_iterate -type npiOperand -refHandle $h]
    if {$it eq ""} {return 0}
    set ok 1
    while {[set part [npi_scan -iterator $it]] ne ""} {
        if {![ct_l_constant_expr $part $seen]} {set ok 0}
    }
    return $ok
}
proc ct_l_transparent {h} {
    if {$h eq ""} {return 0}
    set type [ct_l_prop $h npiType]
    if {$type ne "npiOperation"} {
        if {$type ni {npiNet npiReg npiNetBit npiRegBit npiBitSelect npiPartSelect npiConstant npiParameter npiParameterBit npiRefObj npiIndexedPartSelect}} {return 0}
        # A generate parameter can appear symbolically in npiName even though
        # its value is fixed after elaboration. Ask NPI, never regex the name.
        if {$type in {npiBitSelect npiIndexedPartSelect} && [npi_get -property npiConstantSelect -object $h] ne "1"} {return 0}
        if {$type in {npiNetBit npiRegBit}} {
            # npiIndex is 1-to-many for NetBit/RegBit (manual p3263),
            # unlike the single index on npiBitSelect. Verdi 2018 returns
            # NetBit for both runtime indexes and elaborated genvars.
            set it [npi_iterate -type npiIndex -refHandle $h]
            set ok 1
            if {$it ne ""} {
                while {[set index [npi_scan -iterator $it]] ne ""} {
                    if {![ct_l_constant_expr $index]} {set ok 0}
                }
            }
            if {!$ok} {return 0}
        }
        return 1
    }
    if {[ct_l_prop $h npiOpType] ni {npiConcatOp npiMultiConcatOp npiBitNegOp}} {return 0}
    set it [npi_iterate -type npiOperand -refHandle $h]
    if {$it eq ""} {return 0}
    set ok 1
    while {[set part [npi_scan -iterator $it]] ne ""} {
        if {![ct_l_transparent $part]} {set ok 0}
    }
    return $ok
}
proc ct_l_locator {h scope} {
    set name [ct_l_prop $h npiFullName]
    if {$name eq ""} {
        set expression [::npi_L1::npi_expr_decompile $h]
        if {$expression ne ""} {set name "$scope.$expression"}
    }
    if {$name eq ""} {return ""}
    set check [npi_handle_by_name -name $name -scope ""]
    if {$check eq "" || ![npi_compare_objects -object1 $h -object2 $check]} {return ""}
    return $name
}
proc ct_l_terminal {status fields {evidence ""}} {return [list terminal $status $fields $evidence]}
proc ct_l_boundary {endpoint {evidence ""}} {
    return [ct_l_terminal scope_boundary [list scope [ct_js $::ct_l_root] endpoint [ct_js $endpoint] boundary_role [ct_js input]] $evidence]
}
proc ct_l_note {phase object {api ""} {force 0}} {
    set now [clock milliseconds]
    if {!$force && $now-$::ct_l_note_time < 1000} {return}
    set ::ct_l_note_time $now
    dict set ::ct_walk_stats results $::ct_result_count
    set fields [list trace_id $::ct_trace_id instance [ct_js $::ct_l_owner] pin [ct_js $::ct_l_pin] direction [ct_js driver] \
        phase [ct_js $phase] api [ct_js $api] object [ct_js $object] elapsed_ms [expr {$now-$::ct_walk_started}]]
    dict for {key value} $::ct_walk_stats {lappend fields $key $value}
    set context [ct_object {*}$fields]
    ct_journal progress trace $context "language-model input trace"
    return $context
}
proc ct_l_cache_open {} {
    # This cache is private to the version/input-validated recovery workspace.
    # It is only opened after the entire target set has been restored.
    set path [file join [file dirname $::ct_output] language-steps.cache]
    if {[file exists $path] && [file size $path] <= 8388608} {
        set stream [open $path r]; fconfigure $stream -encoding utf-8
        set record ""
        while {[gets $stream line] >= 0} {
            append record $line \n
            if {![info complete $record]} {continue}
            if {![catch {llength $record} length] && $length == 3 && [lindex $record 0] eq "ct_language_step_v1"} {
                lassign $record version key actions
                dict set ::ct_l_cache $key $actions
                incr ::ct_l_cache_bytes [string bytelength $record]
            }
            set record ""
            if {[dict size $::ct_l_cache] >= 2048 || $::ct_l_cache_bytes >= 4194304} {break}
        }
        close $stream
    }
    # Rewrite a bounded validated prefix; incomplete trailing records vanish.
    set ::ct_l_cache_stream [open $path w]
    fconfigure $::ct_l_cache_stream -encoding utf-8 -translation lf
    dict for {key actions} $::ct_l_cache {
        set record [list ct_language_step_v1 $key $actions]
        puts $::ct_l_cache_stream $record
        incr ::ct_l_cache_written [string bytelength $record]
    }
    flush $::ct_l_cache_stream
}
proc ct_l_cache_put {key actions} {
    if {$key eq ""} {return}
    foreach action $actions {if {[lindex $action 0] eq "handle"} {return}}
    set record [list ct_language_step_v1 $key $actions]
    set bytes [string bytelength $record]
    if {$bytes > 262144} {return}
    if {[dict size $::ct_l_cache] >= 2048 || $::ct_l_cache_bytes+$bytes > 4194304} {
        set ::ct_l_cache [dict create]; set ::ct_l_cache_bytes 0
    }
    dict set ::ct_l_cache $key $actions
    incr ::ct_l_cache_bytes $bytes
    if {$::ct_l_cache_written+$bytes > 8388608} {
        close $::ct_l_cache_stream
        set ::ct_l_cache_stream [open [file join [file dirname $::ct_output] language-steps.cache] w]
        fconfigure $::ct_l_cache_stream -encoding utf-8 -translation lf
        set ::ct_l_cache_written 0
    }
    puts $::ct_l_cache_stream $record
    incr ::ct_l_cache_written $bytes
}
proc ct_l_expand {h} {
    set key [ct_l_key $h]
    if {$key ne "" && [dict exists $::ct_l_cache $key]} {
        dict incr ::ct_l_totals cache_hits
        dict incr ::ct_walk_stats cache_hits
        return [dict get $::ct_l_cache $key]
    }
    set name [ct_l_prop $h npiFullName]
    set indexed [dict create]
    if {$key ne "" && [dict exists $::ct_l_outputs $key]} {
        foreach entry [dict get $::ct_l_outputs $key] {
            lassign $entry instance pin side
            if {$side ne "high"} {continue}
            set candidate [ct_l_port $instance $pin]
            set actual [dict get $candidate high]
            if {$actual ne "" && [npi_compare_objects -object1 $h -object2 $actual]} {
                dict set indexed [list $instance $pin] 1
            }
        }
    }
    ct_l_note driver_begin $name npi_trace_driver_by_hdl2 1
    set rows {}
    ::npi_L1::npi_trace_driver_by_hdl2 $h rows 0
    # Verdi 2018 can return scalar 0 with a nonempty list. Read the list.
    dict incr ::ct_l_totals driver_queries
    dict incr ::ct_walk_stats scanned [llength $rows]
    set actions {}
    foreach row $rows {
        lassign $row src scope pass uses use signals
        set owner [ct_l_prop $scope npiFullName]
        set type [ct_l_prop $use npiType]
        set evidence [ct_l_ev $use "$owner.[ct_l_prop $use npiName]"]
        if {$owner ne "" && ![ct_scope_match $owner $::ct_l_root]} {
            lappend actions [ct_l_boundary $name]; continue
        }
        if {$type eq "npiPort"} {
            set port_name [ct_l_prop $use npiName]
            set direction [ct_l_prop $use npiDirection]
            if {$direction eq "npiOutput" && [dict exists $::ct_l_output_rules $owner]} {
                set actual [npi_handle -type npiHighConn -refHandle $use]
                if {$actual ne "" && ![ct_l_transparent $actual]} {
                    lappend actions [ct_l_terminal unsupported_expression [list endpoint [ct_js "$owner.$port_name"]] $evidence]; continue
                }
                set pin [dict get $::ct_l_output_rules $owner]
                set target [ct_l_port $owner $pin]
                set hit 0
                if {[dict get $target status] eq "ok"} {
                    foreach signal $signals {
                        if {[npi_compare_objects -object1 $signal -object2 [dict get $target low]]} {set hit 1}
                    }
                }
                if {$hit} {
                    dict incr ::ct_l_totals output_index_hits
                    dict unset indexed [list $owner $pin]
                    lappend actions [ct_l_terminal target [list instance [ct_js $owner] pin [ct_js $pin]] $evidence]
                } else {
                    lappend actions [ct_l_terminal unselected_port_boundary [list endpoint [ct_js "$owner.$port_name"] pin_direction [ct_js npiNlOutput]] $evidence]
                }
                continue
            }
            if {$direction eq "npiInput" && $owner eq $::ct_l_root} {
                set endpoint [ct_l_prop $src npiFullName]
                if {$endpoint eq ""} {set endpoint "$owner.$port_name"}
                if {$::ct_trace_scope ne ""} {lappend actions [ct_l_boundary $endpoint $evidence]} else {
                    lappend actions [ct_l_terminal input_boundary [list endpoint [ct_js $endpoint]] $evidence]
                }
                continue
            }
            set high [npi_handle -type npiHighConn -refHandle $use]
            if {$direction eq "npiInput" && $high eq ""} {
                lappend actions [ct_l_terminal undriven [list endpoint [ct_js "$owner.$port_name"]] $evidence]; continue
            }
            if {$high ne "" && ![ct_l_transparent $high]} {
                lappend actions [ct_l_terminal unsupported_expression [list endpoint [ct_js "$owner.$port_name"]] $evidence]; continue
            }
        } elseif {$type eq "npiContAssign"} {
            if {![ct_l_transparent [npi_handle -type npiRhs -refHandle $use]]} {
                lappend actions [ct_l_terminal unknown_cell [list cell [ct_js $type] endpoint [ct_js $owner]] $evidence]; continue
            }
        } elseif {$type eq "npiPrimTerm"} {
            set primitive [npi_handle -type npiPrimitive -refHandle $use]
            set prim_type [ct_l_prop $primitive npiPrimType]
            if {$prim_type ni {npiBufPrim npiNotPrim}} {
                lappend actions [ct_l_terminal unknown_cell [list cell [ct_js $prim_type] endpoint [ct_js [ct_l_prop $primitive npiFullName]]] $evidence]; continue
            }
        } else {
            lappend actions [ct_l_terminal unknown_cell [list cell [ct_js $type] endpoint [ct_js $owner]] $evidence]; continue
        }
        if {![llength $signals]} {
            lappend actions [ct_l_terminal unknown_type [list endpoint [ct_js $name]] $evidence]
        }
        foreach signal $signals {
            if {[ct_l_prop $signal npiType] in {npiConstant npiParameter npiParameterBit}} {
                lappend actions [ct_l_terminal constant [list endpoint [ct_js [::npi_L1::npi_expr_decompile $signal]]] $evidence]
            } else {
                set locator [ct_l_locator $signal $owner]
                if {$locator ne ""} {lappend actions [list next $locator {} $evidence]} else {lappend actions [list handle $signal {} $evidence]}
            }
        }
    }
    if {[dict size $indexed]} {
        lappend actions [ct_l_terminal error [list message [ct_js "Language driver did not confirm all indexed output connections: [dict keys $indexed]"]]]
    }
    if {![llength $actions]} {lappend actions [ct_l_terminal undriven [list endpoint [ct_js $name]]]}
    ct_l_cache_put $key $actions
    ct_l_note driver_end $name npi_trace_driver_by_hdl2
    return $actions
}
proc ct_l_step {parent evidence} {
    incr ::ct_l_path_count
    ct_emit [ct_object event [ct_js path_step] trace_id $::ct_trace_id path_id $::ct_l_path_count parent_id $parent evidence $evidence]
    return $::ct_l_path_count
}
proc ct_l_walk {owner pin trace_id} {
    set ::ct_trace_id $trace_id
    set ::ct_l_owner $owner; set ::ct_l_pin $pin
    set ::ct_walk_started [clock milliseconds]; set ::ct_l_note_time 0
    set ::ct_walk_stats [dict create processed 0 discovered 0 pending 0 scanned 0 duplicates 0 results 0 cache_hits 0]
    set ::ct_result_count 0; set ::ct_l_path_count 0
    ct_emit [ct_object event [ct_js trace_begin] trace_id $trace_id instance [ct_js $owner] pin [ct_js $pin] direction [ct_js driver]]
    ct_journal item_begin trace [ct_object trace_id $trace_id instance [ct_js $owner] pin [ct_js $pin] direction [ct_js driver]] "trace target input"
    ct_l_note bind "$owner.$pin" npiHighConn 1
    set data [ct_l_port $owner $pin]
    set h [dict get $data high]
    if {$h eq "" || [ct_l_prop $h npiType] eq "npiOperation"} {set h [dict get $data low]}
    set queue [dict create 0 [list $h 0 "$owner.$pin"]]
    set head 0; set tail 1; set seen [dict create]
    while {$head < $tail} {
        lassign [dict get $queue $head] h parent last_inside
        dict unset queue $head; incr head
        dict incr ::ct_walk_stats processed
        dict set ::ct_walk_stats pending [expr {$tail-$head}]
        if {$::ct_max_steps > 0 && $head > $::ct_max_steps} {ct_terminal [ct_result trace_limit $parent]; break}
        set name [ct_l_prop $h npiFullName]
        set type [ct_l_prop $h npiType]
        if {$type eq "npiNIY"} {
            ct_terminal [ct_result unsupported_expression $parent endpoint [ct_js "$owner.$pin"] \
                message [ct_js "Verdi Language Model returned npiNIY (not implemented); connection mapping cannot be proven. Use full verification for this design."]]
            continue
        }
        if {$type in {npiConstant npiParameter npiParameterBit}} {
            set path [ct_l_step $parent [ct_object type [ct_js $type] name [ct_js [::npi_L1::npi_expr_decompile $h]] net [ct_js ""]]]
            ct_terminal [ct_result constant $path endpoint [ct_js [::npi_L1::npi_expr_decompile $h]]]; continue
        }
        if {$name ne "" && ![ct_scope_match $name $::ct_l_root]} {ct_terminal [ct_result scope_boundary $parent scope [ct_js $::ct_l_root] endpoint [ct_js $last_inside] boundary_role [ct_js input]]; continue}
        set key [ct_l_key $h]
        if {$key eq ""} {set key [list handle $h]}
        if {[dict exists $seen $key]} {dict incr ::ct_walk_stats duplicates; continue}
        dict set seen $key 1
        set path [ct_l_step $parent [ct_l_ev $h]]
        if {![ct_l_transparent $h]} {ct_terminal [ct_result unsupported_expression $path endpoint [ct_js $name]]; continue}
        if {$type eq "npiOperation" || [ct_l_size $h] != 1} {ct_terminal [ct_result unsupported_expression $path endpoint [ct_js [::npi_L1::npi_expr_decompile $h]]]; continue}
        if {[catch {ct_l_expand $h} actions]} {ct_terminal [ct_result error $path message [ct_js $actions]]; continue}
        foreach action $actions {
            lassign $action kind value fields evidence
            set step $path
            if {$evidence ne ""} {set step [ct_l_step $path $evidence]}
            if {$kind eq "terminal"} {ct_terminal [ct_result $value $step {*}$fields]} else {
                if {$kind eq "next"} {set child [npi_handle_by_name -name $value -scope ""]} else {set child $value}
                if {$child eq ""} {ct_terminal [ct_result error $step message [ct_js "Cannot restore language signal $value"]]; continue}
                dict set queue $tail [list $child $step [expr {$name ne "" ? $name : $last_inside}]]
                incr tail; dict incr ::ct_walk_stats discovered
            }
        }
    }
    if {!$::ct_result_count} {ct_terminal [ct_result error 0 message [ct_js "No target or boundary; possible alias cycle"]]}
    ct_emit [ct_object event [ct_js trace_end] trace_id $trace_id result_count $::ct_result_count path_steps $::ct_l_path_count]
    flush $::ct_stream; flush $::ct_l_cache_stream
    set context [ct_l_note complete "$owner.$pin" "" 1]
    ct_journal item_end trace $context "target input trace complete"
    npi_release_all_handles
}
