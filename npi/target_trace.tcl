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
        set connected [expr {$high ne ""}]
        if {$low ne "" && $bit ne ""} {
            set low [npi_handle_by_index -object $low -index $bit]
            # A whole actual bus is not the selected formal bit. Start from
            # its low connection so the native language API maps the bit.
            set high ""
        }
        set status ok
        if {$low eq ""} {set status missing_pin} elseif {[ct_l_size $low] != 1} {set status width_requires_bit}
        set result [dict create status $status low $low high $high port $p connected $connected direction [ct_l_prop $p npiDirection]]
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
    set ::ct_l_wire_inputs [dict create]
    set ::ct_l_internal 0
    set ::ct_l_formal_inputs [dict create]
    set ::ct_l_boundary_port_nets [dict create]
    set ::ct_nl_wire_cache [dict create]
    set ::ct_l1_open_inputs [dict create]
    set ::ct_l_interface_ports [dict create]
    set ::ct_l_cache [dict create]
    set ::ct_l_cache_bytes 0
    set ::ct_l_cache_written 0
    set ::ct_l_totals [dict create driver_queries 0 cache_hits 0 output_index_hits 0]
    set ::ct_l_root [expr {$::ct_trace_scope ne "" ? $::ct_trace_scope : $::ct_top}]
}
proc ct_l_index_input_ports {} {
    set scopes [dict keys $::ct_input_boundaries]
    if {$::ct_l1_scope ne ""} {lappend scopes $::ct_l1_scope}
    foreach scope $scopes {
        set root [npi_handle_by_name -name $scope -scope ""]
        set configured [ct_configured_input_scope $scope]
        if {$configured && [ct_l_prop $root npiType] ne "npiModule"} {
            error "L[dict get $::ct_input_boundaries $scope] input boundary is not an elaborated RTL module: $scope"
        }
        if {$root eq ""} {continue}
        if {$configured} {ct_l_index_boundary_ports $root $scope}
        # Index only formal input bits of the named module.
        set read_binding [expr {!$configured && $::ct_extraction_mode eq "full" && $scope ne $::ct_l_root}]
        foreach record [ct_input_ports $root npiInput $read_binding] {
            lassign $record pin width left right connected
            set open [expr {$read_binding && !$connected}]
            if {$width == 1 && $left eq ""} {
                dict set ::ct_l_formal_inputs "$scope.$pin" [list $scope $pin]
                if {$open} {dict set ::ct_l1_open_inputs $pin 1}
            } elseif {[string is integer -strict $left] && [string is integer -strict $right] && abs($left-$right)+1 == $width} {
                set step [expr {$left <= $right ? 1 : -1}]
                for {set i $left} {1} {incr i $step} {
                    set bit "$pin\[$i\]"
                    dict set ::ct_l_formal_inputs "$scope.$bit" [list $scope $bit]
                    if {$open} {dict set ::ct_l1_open_inputs $bit 1}
                    if {$i == $right} {break}
                }
                if {$width == 1} {dict set ::ct_l_formal_inputs "$scope.$pin" [list $scope $bit]}
            }
        }
        npi_release_handle -object $root
    }
}
proc ct_l_index_boundary_ports {module scope} {
    set it [npi_iterate -type npiPort -refHandle $module]
    if {$it eq ""} {return}
    while {[set port [npi_scan -iterator $it]] ne ""} {
        dict set ::ct_l_boundary_port_nets "$scope.[ct_l_prop $port npiName]" 1
        set low [npi_handle -type npiLowConn -refHandle $port]
        if {[ct_l_prop $low npiType] in {npiNet npiNetBit}} {
            set name [ct_l_prop $low npiFullName]
            regsub {\[-?[0-9]+\]$} $name "" name
            if {$name ne ""} {dict set ::ct_l_boundary_port_nets $name 1}
        }
        if {$low ne ""} {npi_release_handle -object $low}
        npi_release_handle -object $port
    }
}
proc ct_l_wire_select {h type} {
    if {[ct_l_size $h] != 1 || ![ct_l_transparent $h]} {return {}}
    set parent [npi_handle -type npiParent -refHandle $h]
    if {[ct_l_prop $parent npiType] ne "npiNet"} {
        if {$parent ne ""} {npi_release_handle -object $parent}
        return {}
    }
    set relation [dict get {npiBitSelect npiIndex npiPartSelect npiLeftRange npiIndexedPartSelect npiBaseExpr} $type]
    set index [npi_handle -type $relation -refHandle $h]
    set bit ""
    if {$index ne "" && [ct_l_constant_expr $index] &&
            ![catch {npi_get_value -format npiDecStrVal -object $index} value] && [string is integer -strict $value]} {
        set bit [npi_handle_by_index -object $parent -index $value]
    }
    if {$index ne ""} {npi_release_handle -object $index}
    npi_release_handle -object $parent
    set entry {}
    if {[ct_l_prop $bit npiType] eq "npiNetBit"} {set entry [ct_l_wire_boundary $bit]}
    if {$bit ne ""} {npi_release_handle -object $bit}
    return $entry
}
proc ct_l_wire_boundary {h} {
    set type [ct_l_prop $h npiType]
    if {$type in {npiBitSelect npiPartSelect npiIndexedPartSelect}} {return [ct_l_wire_select $h $type]}
    if {$type ni {npiNet npiNetBit}} {return {}}
    set owner [npi_handle -type npiScope -refHandle $h]
    set scope [ct_l_prop $owner npiFullName]
    set module [expr {[ct_l_prop $owner npiType] eq "npiModule"}]
    if {$owner ne ""} {npi_release_handle -object $owner}
    if {!$module || ![ct_configured_input_scope $scope]} {return {}}
    set name [ct_l_prop $h npiFullName]
    set prefix "$scope."
    if {[string first $prefix $name] != 0} {return {}}
    set base [regsub {\[-?[0-9]+\]$} $name ""]
    if {[dict exists $::ct_l_boundary_port_nets $base] || [ct_l_size $h] != 1 || ![ct_l_transparent $h]} {return {}}
    return [list $scope [string range $name [string length $prefix] end]]
}
proc ct_l_index_interface_ports {module owner} {
    if {[dict exists $::ct_l_interface_ports $owner]} {return}
    set ports [dict create]
    set it [npi_iterate -type npiPort -refHandle $module]
    if {$it ne ""} {
        while {[set port [npi_scan -iterator $it]] ne ""} {
            set type [ct_l_prop $port npiPortType]
            if {$type in {npiInterfacePort npiModportPort}} {
                set binding ""
                if {[info exists ::ct_input_boundaries] && [dict size $::ct_input_boundaries]} {
                    set high [npi_handle -type npiHighConn -refHandle $port]
                    set binding [ct_l_prop $high npiFullName]
                    if {$high ne ""} {npi_release_handle -object $high}
                }
                dict set ports [ct_l_prop $port npiName] $binding
            }
            npi_release_handle -object $port
        }
    }
    # Keep only port descriptors and bound the cache for large designs.
    if {[dict size $::ct_l_interface_ports] >= 2048} {set ::ct_l_interface_ports [dict create]}
    dict set ::ct_l_interface_ports $owner $ports
}
proc ct_l_interface_member {h} {
    # An instance of an interface is different from a module's formal
    # interface port. The Language Model distinguishes them via npiScope.
    if {[ct_l_prop $h npiType] ne "npiRefObj"} {return {}}
    set scope [npi_handle -type npiScope -refHandle $h]
    set scope_type [ct_l_prop $scope npiType]
    if {$scope_type ni {npiRefObj npiInterface}} {return {}}
    set name [ct_l_prop $h npiFullName]
    set object [ct_l_prop $scope npiFullName]
    if {$object eq "" || [string first "$object." $name] != 0} {return {}}
    set member [string range $name [expr {[string length $object]+1}] end]
    if {![regexp {^[a-zA-Z_$][a-zA-Z0-9_$]*(?:\[(-?[0-9]+)\])?$} $member -> bit]} {return {}}
    set actual [npi_handle -type npiActual -refHandle $h]
    set actual_type [ct_l_prop $actual npiType]
    if {$actual_type eq "npiMpPortBit"} {
        set port [npi_handle -type npiParent -refHandle $actual]
    } else {set port $actual}
    if {$scope_type eq "npiInterface"} {
        if {$actual_type ni {npiNet npiReg npiNetBit npiRegBit} || [ct_l_size $actual] != 1} {return {}}
        return [dict create signal $actual direction local owner "" formal ""]
    }
    set module [npi_handle -type npiScope -refHandle $scope]
    if {[ct_l_prop $module npiType] ne "npiModule"} {return {}}
    set owner [ct_l_prop $module npiFullName]
    if {[string first "$owner." $object] != 0} {return {}}
    set interface [string range $object [expr {[string length $owner]+1}] end]
    if {![regexp {^([a-zA-Z_$][a-zA-Z0-9_$]*)(?:\[-?[0-9]+\])*$} $interface -> base]} {return {}}
    ct_l_index_interface_ports $module $owner
    if {![dict exists $::ct_l_interface_ports $owner $base]} {return {}}
    set direction unspecified
    set target $actual
    if {[ct_l_prop $port npiType] eq "npiMpPort"} {
        if {$bit eq "" && [ct_l_size $port] != 1} {return {}}
        set direction [ct_l_prop $port npiDirection]
        set target [npi_handle -type npiExpr -refHandle $port]
        if {$bit ne "" && $actual_type eq "npiMpPortBit" && [ct_l_prop $target npiType] in {npiNet npiReg}} {
            # The same declared index identifies a bit of an unaliased
            # member. The generic driver wrapper reverses some ascending buses.
            set target [npi_handle_by_index -object $target -index $bit]
        } elseif {$bit ne ""} {set target ""}
    }
    if {$target eq "" || [ct_l_prop $target npiType] ni {npiNet npiReg npiNetBit npiRegBit} || [ct_l_size $target] != 1} {
        if {$direction eq "unspecified"} {return {}}
        set target ""
    }
    if {$direction eq "unspecified"} {
        # An unrestricted interface can be driven by the module. Check the
        # formal reference itself before following a possible incoming wire.
        set uses {}
        ::npi_L1::npi_trace_driver_by_hdl2 $h uses 0
        if {[llength $uses]} {set direction npiOutput}
    }
    set binding [dict get $::ct_l_interface_ports $owner $base]
    return [dict create signal $target direction $direction owner $owner formal "$interface.$member" \
        binding [expr {$binding eq "" ? "" : "$binding.$member"}]]
}
proc ct_l_formal_input {h} {
    set name [ct_l_prop $h npiFullName]
    if {![dict exists $::ct_l_formal_inputs $name] || [ct_l_size $h] != 1 || ![ct_l_transparent $h]} {return {}}
    set output_key [ct_l_key $h]
    if {$output_key ne "" && [dict exists $::ct_l_outputs $output_key]} {return {}}
    set formal [npi_handle_by_name -name $name -scope ""]
    if {$formal eq ""} {return {}}
    set match [npi_compare_objects -object1 $h -object2 $formal]
    npi_release_handle -object $formal
    if {!$match} {return {}}
    return [dict get $::ct_l_formal_inputs $name]
}
proc ct_l_interface_input {direction} {return [expr {$direction in {npiInput unspecified}}]}
proc ct_l_interface_signal {h} {
    set type [ct_l_prop $h npiType]
    if {$type ni {npiNet npiReg npiNetBit npiRegBit}} {return 0}
    return [expr {[ct_l_prop [npi_handle -type npiScope -refHandle $h] npiType] eq "npiInterface"}]
}
proc ct_l_bounds {h} {
    lassign [ct_declared_range $h] left right
    if {![string is integer -strict $left] || ![string is integer -strict $right] ||
            abs($left-$right)+1 != [ct_l_size $h]} {return {}}
    return [list $left $right]
}
proc ct_l_expression_bit {h offset} {
    # Offset is measured from the right-hand end of the expression.
    set width [ct_l_size $h]
    if {![string is integer -strict $width] || $offset < 0 || $offset >= $width} {return ""}
    if {$width == 1} {return $h}
    set type [ct_l_prop $h npiType]
    if {$type in {npiNet npiReg}} {
        lassign [ct_l_bounds $h] left right
        if {$left eq ""} {return ""}
        set index [expr {$left < $right ? $right-$offset : $right+$offset}]
        return [npi_handle_by_index -object $h -index $index]
    }
    if {$type ne "npiOperation" || [ct_l_prop $h npiOpType] ne "npiConcatOp"} {return ""}
    set position [expr {$width-1-$offset}]
    set it [npi_iterate -type npiOperand -refHandle $h]
    if {$it eq ""} {return ""}
    while {[set part [npi_scan -iterator $it]] ne ""} {
        set size [ct_l_size $part]
        if {![string is integer -strict $size]} {break}
        if {$position < $size} {
            npi_release_handle -object $it
            return [ct_l_expression_bit $part [expr {$size-1-$position}]]
        }
        incr position -$size
    }
    return ""
}
proc ct_l_interface_assignment_bit {bit vector rhs} {
    if {$rhs eq "" || [ct_l_size $rhs] != [ct_l_size $vector]} {return ""}
    lassign [ct_l_bounds $vector] left right
    if {$left eq "" || ![regexp {\[(-?[0-9]+)\]$} [ct_l_prop $bit npiName] -> index]} {return ""}
    return [ct_l_expression_bit $rhs [expr {abs($index-$right)}]]
}
proc ct_l_interface_drivers {h rows} {
    set drivers {}
    foreach row $rows {
        lassign $row src scope pass uses use signals
        if {[ct_l_prop $use npiType] ne "npiMpPort" || [ct_l_prop $use npiDirection] ne "npiOutput"} {
            lappend drivers $row
            continue
        }
        # Resolve the native modport view to its actual driving statement.
        foreach signal $signals {
            set through {}
            ::npi_L1::npi_trace_driver_by_hdl2 $signal through 1
            dict incr ::ct_l_totals driver_queries
            foreach item $through {lappend drivers $item}
        }
    }
    # The 2018 driver wrapper can omit direct hierarchical writes to a
    # member. Consider only assignments whose bound LHS covers this bit.
    # Use the single member's streaming iterator; never query its loads.
    set seen [dict create]
    foreach row $drivers {dict set seen [lindex $row 4] 1}
    set vector [npi_handle -type npiParent -refHandle $h]
    set it [npi_iterate -type npiUse -refHandle $h]
    if {$it eq ""} {return $drivers}
    while {[set use [npi_scan -iterator $it]] ne ""} {
        if {[dict exists $seen $use]} {continue}
        if {[ct_l_prop $use npiType] ni {npiContAssign npiAssignment}} {npi_release_handle -object $use; continue}
        set lhs [npi_handle -type npiLhs -refHandle $use]
        if {[ct_l_prop $lhs npiType] ne "npiExtRef"} {
            if {$lhs ne ""} {npi_release_handle -object $lhs}
            npi_release_handle -object $use
            continue
        }
        set actual [npi_handle -type npiActual -refHandle $lhs]
        set exact [expr {$actual ne "" && [npi_compare_objects -object1 $actual -object2 $h]}]
        set whole [expr {$actual ne "" && $vector ne "" && [npi_compare_objects -object1 $actual -object2 $vector]}]
        if {!$exact && !$whole} {
            if {$actual ne ""} {npi_release_handle -object $actual}
            npi_release_handle -object $lhs
            npi_release_handle -object $use
            continue
        }
        set rhs [npi_handle -type npiRhs -refHandle $use]
        if {$exact} {
            set signal $rhs
        } else {
            set signal [ct_l_interface_assignment_bit $h $vector $rhs]
        }
        set owner [npi_handle -type npiScope -refHandle $use]
        lappend drivers [list $h $owner 0 1 $use [expr {$signal eq "" ? {} : [list $signal]}]]
    }
    return $drivers
}
proc ct_l_register_wire {path outputs} {
    foreach pin $outputs {
        dict set ::ct_pins_to_trace $::ct_pin_count [list "" internal $path $pin]
        incr ::ct_pin_count
    }
}
proc ct_l_register {path inputs outputs emit} {
    global ct_pins_to_trace ct_pin_count ct_l_outputs ct_l_output_rules
    set pins [concat $inputs $outputs]
    if {[llength $outputs]} {dict set ct_l_output_rules $path $outputs}
    foreach pin $pins {
        set role [expr {$pin in $outputs ? "output" : "input"}]
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
    if {$type eq "npiExtRef"} {
        # A direct reference to an interface member is a signal alias.
        return [ct_l_interface_signal [npi_handle -type npiActual -refHandle $h]]
    }
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
    set allowed {npiConcatOp npiMultiConcatOp}
    if {!$::ct_l_internal} {lappend allowed npiBitNegOp}
    if {[ct_l_prop $h npiOpType] ni $allowed} {return 0}
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
proc ct_l_terminal {status fields {evidence ""} {past_port 0}} {return [list terminal $status $fields $evidence $past_port]}
proc ct_l_boundary {endpoint {evidence ""}} {
    return [ct_l_terminal scope_boundary [list scope [ct_js $::ct_l_root] endpoint [ct_js $endpoint] boundary_role [ct_js input]] $evidence]
}
proc ct_l_note {phase object {api ""} {force 0}} {
    set now [clock milliseconds]
    if {!$force && $now-$::ct_l_note_time < 1000} {return}
    set ::ct_l_note_time $now
    dict set ::ct_walk_stats results $::ct_result_count
    set fields [list trace_id $::ct_trace_id instance [ct_js $::ct_l_owner] pin [ct_js $::ct_l_pin] direction [ct_js [expr {$::ct_l_internal ? "internal" : "driver"}]] \
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
            if {![catch {llength $record} length] && $length == 3 && [lindex $record 0] eq "ct_language_step_v6"} {
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
        set record [list ct_language_step_v6 $key $actions]
        puts $::ct_l_cache_stream $record
        incr ::ct_l_cache_written [string bytelength $record]
    }
    flush $::ct_l_cache_stream
}
proc ct_l_cache_put {key actions} {
    if {$key eq ""} {return}
    foreach action $actions {if {[lindex $action 0] eq "handle"} {return}}
    set record [list ct_language_step_v6 $key $actions]
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
proc ct_l_expand {h {member_signal ""}} {
    set key [ct_l_key $h]
    if {$key ne "" && $::ct_l_internal} {set key [list internal $::ct_l_root $key]}
    if {$key ne "" && [dict exists $::ct_l_cache $key]} {
        dict incr ::ct_l_totals cache_hits
        dict incr ::ct_walk_stats cache_hits
        return [dict get $::ct_l_cache $key]
    }
    set name [ct_l_prop $h npiFullName]
    set indexed [dict create]
    if {!$::ct_l_internal && $key ne "" && [dict exists $::ct_l_outputs $key]} {
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
    if {[ct_l_interface_signal $h]} {
        ct_l_note interface_driver_begin $name npiUse 1
        set rows [ct_l_interface_drivers $h $rows]
        set member_signal $h
    }
    set member_vector ""
    if {[ct_l_prop $member_signal npiType] in {npiNetBit npiRegBit}} {
        set member_vector [npi_handle -type npiParent -refHandle $member_signal]
    }
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
        set l1_entry {}
        set past_port 0
        if {$type eq "npiPort"} {
            set port_name [ct_l_prop $use npiName]
            set direction [ct_l_prop $use npiDirection]
            if {!$::ct_l_internal && $direction eq "npiInput" && [ct_tracked_input_scope $owner]} {
                # The driver row identifies the exact formal bit.
                set is_boundary [ct_is_input_boundary $owner $::ct_l_root]
                set endpoint [ct_l_prop $src npiFullName]
                if {$endpoint eq ""} {set endpoint "$owner.$port_name"}
                if {$src eq "" || [ct_l_size $src] != 1 || ![dict exists $::ct_l_formal_inputs $endpoint] ||
                        [lindex [dict get $::ct_l_formal_inputs $endpoint] 0] ne $owner} {
                    if {$is_boundary} {
                        lappend actions [ct_l_terminal unsupported_expression [list endpoint [ct_js $endpoint]] $evidence]
                        continue
                    }
                } else {
                    set l1_entry [ct_boundary_fields $owner [lindex [dict get $::ct_l_formal_inputs $endpoint] 1]]
                    if {$is_boundary} {
                        lappend actions [ct_l_terminal [ct_input_status $owner] $l1_entry $evidence]
                        if {$owner eq $::ct_l_root} {continue}
                        set l1_entry {}
                        set past_port 1
                    }
                }
            }
            if {!$::ct_l_internal && $direction eq "npiOutput" && [dict exists $::ct_l_output_rules $owner]} {
                set actual [npi_handle -type npiHighConn -refHandle $use]
                if {$actual ne "" && ![ct_l_transparent $actual]} {
                    lappend actions [ct_l_terminal unsupported_expression [list endpoint [ct_js "$owner.$port_name"]] $evidence]; continue
                }
                set hit 0
                foreach pin [dict get $::ct_l_output_rules $owner] {
                    set target [ct_l_port $owner $pin]
                    set pin_hit 0
                    if {[dict get $target status] eq "ok"} {
                        foreach signal $signals {
                            if {[npi_compare_objects -object1 $signal -object2 [dict get $target low]]} {set pin_hit 1}
                        }
                    }
                    ct_l_free_port $target
                    if {$pin_hit} {
                        set hit 1
                        dict incr ::ct_l_totals output_index_hits
                        dict unset indexed [list $owner $pin]
                        lappend actions [ct_l_terminal target [list instance [ct_js $owner] pin [ct_js $pin]] $evidence]
                    }
                }
                if {!$hit} {
                    lappend actions [ct_l_terminal unselected_port_boundary [list endpoint [ct_js "$owner.$port_name"] pin_direction [ct_js npiNlOutput]] $evidence]
                }
                continue
            }
            if {$direction eq "npiInput" && $owner eq $::ct_l_root} {
                if {$::ct_l_internal} {
                    set hit 0
                    foreach pin [dict get $::ct_l_wire_inputs $owner] {
                        set target [ct_l_port $owner $pin]
                        if {[dict get $target status] eq "ok"} {
                            foreach signal [concat [list $src] $signals] {
                                if {$signal ne "" && [npi_compare_objects -object1 $signal -object2 [dict get $target low]]} {
                                    set hit 1
                                    lappend actions [ct_l_terminal target [list instance [ct_js $owner] pin [ct_js $pin]] $evidence]
                                    break
                                }
                            }
                        }
                        ct_l_free_port $target
                    }
                    if {!$hit} {lappend actions [ct_l_terminal unselected_wire_input [list endpoint [ct_js "$owner.$port_name"]] $evidence]}
                    continue
                }
                set endpoint [ct_l_prop $src npiFullName]
                if {$endpoint eq ""} {set endpoint "$owner.$port_name"}
                if {$::ct_trace_scope ne ""} {lappend actions [ct_l_boundary $endpoint $evidence]} else {
                    lappend actions [ct_l_terminal input_boundary [list endpoint [ct_js $endpoint]] $evidence]
                }
                continue
            }
            set high [npi_handle -type npiHighConn -refHandle $use]
            if {$direction eq "npiInput" && $high eq ""} {
                lappend actions [ct_l_terminal undriven [list endpoint [ct_js "$owner.$port_name"]] $evidence $past_port]; continue
            }
            if {$high ne "" && ![ct_l_transparent $high]} {
                if {[llength $l1_entry]} {lappend actions [list l1_entry [ct_input_status $owner] $l1_entry $evidence]}
                lappend actions [ct_l_terminal unsupported_expression [list endpoint [ct_js "$owner.$port_name"]] $evidence $past_port]; continue
            }
        } elseif {$type eq "npiContAssign"} {
            set rhs [npi_handle -type npiRhs -refHandle $use]
            if {![ct_l_transparent $rhs]} {
                lappend actions [ct_l_terminal unknown_cell [list cell [ct_js $type] endpoint [ct_js $owner]] $evidence]; continue
            }
            if {$member_vector ne ""} {
                set lhs [npi_handle -type npiLhs -refHandle $use]
                if {[ct_l_prop $lhs npiType] eq "npiRefObj"} {
                    set bound [npi_handle -type npiActual -refHandle $lhs]
                    if {[ct_l_prop $bound npiType] eq "npiMpPort"} {set bound [npi_handle -type npiExpr -refHandle $bound]}
                    if {$bound ne "" && [npi_compare_objects -object1 $bound -object2 $member_vector]} {
                        # The driver row identifies the assignment. Derive its
                        # RHS bit from the declared range, not the ordinal
                        # returned by the 2018 wrapper for a modport reference.
                        set signal [ct_l_interface_assignment_bit $member_signal $member_vector $rhs]
                        set signals [expr {$signal eq "" ? {} : [list $signal]}]
                    }
                }
            }
        } elseif {$type eq "npiPrimTerm"} {
            set primitive [npi_handle -type npiPrimitive -refHandle $use]
            set prim_type [ct_l_prop $primitive npiPrimType]
            if {$prim_type ni {npiBufPrim npiNotPrim} || ($::ct_l_internal && $prim_type ne "npiBufPrim")} {
                lappend actions [ct_l_terminal unknown_cell [list cell [ct_js $prim_type] endpoint [ct_js [ct_l_prop $primitive npiFullName]]] $evidence]; continue
            }
        } else {
            lappend actions [ct_l_terminal unknown_cell [list cell [ct_js $type] endpoint [ct_js $owner]] $evidence]; continue
        }
        if {[llength $l1_entry]} {lappend actions [list l1_entry [ct_input_status $owner] $l1_entry $evidence]}
        if {![llength $signals]} {
            lappend actions [ct_l_terminal unknown_type [list endpoint [ct_js $name]] $evidence $past_port]
        }
        foreach signal $signals {
            if {[ct_l_prop $signal npiType] in {npiConstant npiParameter npiParameterBit}} {
                lappend actions [ct_l_terminal constant [list endpoint [ct_js [::npi_L1::npi_expr_decompile $signal]]] $evidence $past_port]
            } else {
                set locator [ct_l_locator $signal $owner]
                if {$locator ne ""} {lappend actions [list next $locator {} $evidence $past_port]} else {lappend actions [list handle $signal {} $evidence $past_port]}
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
proc ct_l_walk {owner pin trace_id {direction driver}} {
    set ::ct_l_internal [expr {$direction eq "internal"}]
    set ::ct_l_root [expr {$::ct_l_internal ? $owner : ($::ct_trace_scope ne "" ? $::ct_trace_scope : $::ct_top)}]
    set check_wires [expr {!$::ct_l_internal && [dict size $::ct_input_boundaries] > 0}]
    set check_formal [expr {!$::ct_l_internal && ($::ct_l_root eq $::ct_l1_scope || $check_wires)}]
    set ::ct_trace_id $trace_id
    set ::ct_l_owner $owner; set ::ct_l_pin $pin
    set ::ct_walk_started [clock milliseconds]; set ::ct_l_note_time 0
    set ::ct_walk_stats [dict create processed 0 discovered 0 pending 0 scanned 0 duplicates 0 results 0 cache_hits 0]
    set ::ct_result_count 0; set ::ct_l_path_count 0
    ct_emit [ct_object event [ct_js trace_begin] trace_id $trace_id instance [ct_js $owner] pin [ct_js $pin] direction [ct_js $direction]]
    ct_journal item_begin trace [ct_object trace_id $trace_id instance [ct_js $owner] pin [ct_js $pin] direction [ct_js $direction]] "trace port connectivity"
    ct_l_note bind "$owner.$pin" npiHighConn 1
    set data [ct_l_port $owner $pin]
    set h [dict get $data high]
    set boundary_seed [expr {!$::ct_l_internal && [ct_configured_input_scope $owner] &&
        [dict get $data status] eq "ok" && [dict get $data direction] eq "npiInput" &&
        [dict exists $::ct_l_formal_inputs "$owner.$pin"]}]
    if {$boundary_seed || $::ct_l_internal || $h eq "" || [ct_l_prop $h npiType] eq "npiOperation"} {
        set h [dict get $data low]
    }
    set queue [dict create 0 [list $h 0 "$owner.$pin" 0]]
    set head 0; set tail 1; set seen [dict create]
    while {$head < $tail} {
        lassign [dict get $queue $head] h parent last_inside past_boundary
        dict unset queue $head; incr head
        dict incr ::ct_walk_stats processed
        dict set ::ct_walk_stats pending [expr {$tail-$head}]
        if {$::ct_max_steps > 0 && $head > $::ct_max_steps} {
            ct_terminal [ct_result trace_limit $parent]; break
        }
        set name [ct_l_prop $h npiFullName]
        set type [ct_l_prop $h npiType]
        if {$type eq "npiNIY"} {
            ct_walk_result $past_boundary unsupported_expression $parent endpoint [ct_js "$owner.$pin"] \
                message [ct_js "Verdi Language Model returned npiNIY (not implemented); connection mapping cannot be proven. Use full verification for this design."]
            continue
        }
        if {$type in {npiConstant npiParameter npiParameterBit}} {
            set path [ct_l_step $parent [ct_object type [ct_js $type] name [ct_js [::npi_L1::npi_expr_decompile $h]] net [ct_js ""]]]
            ct_walk_result $past_boundary constant $path endpoint [ct_js [::npi_L1::npi_expr_decompile $h]]; continue
        }
        if {$name ne "" && ![ct_scope_match $name $::ct_l_root]} {ct_walk_result $past_boundary scope_boundary $parent scope [ct_js $::ct_l_root] endpoint [ct_js $last_inside] boundary_role [ct_js input]; continue}
        set key [ct_l_key $h]
        if {$key eq ""} {set key [list handle $h]}
        if {$past_boundary} {lappend key past_boundary}
        if {[dict exists $seen $key]} {dict incr ::ct_walk_stats duplicates; continue}
        dict set seen $key 1
        set path [ct_l_step $parent [ct_l_ev $h]]
        if {$head == 1 && $boundary_seed} {
            set formal [lindex [dict get $::ct_l_formal_inputs "$owner.$pin"] 1]
            ct_terminal [ct_result [ct_input_status $owner] $path {*}[ct_boundary_fields $owner $formal]]
            set past_boundary 1
        }
        if {$type eq "npiExtRef" && [ct_l_transparent $h]} {
            set actual [npi_handle -type npiActual -refHandle $h]
            if {[ct_l_size $actual] == 1} {
                dict set queue $tail [list $actual $path $last_inside $past_boundary]
                incr tail; dict incr ::ct_walk_stats discovered
                continue
            }
        }
        set member [ct_l_interface_member $h]
        set member_signal ""
        if {[dict size $member]} {
            set member_owner [dict get $member owner]
            set member_direction [dict get $member direction]
            set member_signal [dict get $member signal]
            set formal [dict get $member formal]
            set is_ingress [ct_l_interface_input $member_direction]
            if {!$::ct_l_internal && $is_ingress && [ct_tracked_input_scope $member_owner]} {
                set fields [ct_boundary_fields $member_owner $formal]
                if {[ct_is_input_boundary $member_owner $::ct_l_root]} {
                    ct_walk_result $past_boundary [ct_input_status $member_owner] $path {*}$fields
                    if {$member_owner eq $::ct_l_root} {continue}
                    set past_boundary 1
                } elseif {!$past_boundary} {
                    ct_emit [ct_object event [ct_js l1_entry] trace_id $trace_id result [ct_result [ct_input_status $member_owner] $path {*}$fields]]
                }
            } elseif {$is_ingress && $member_owner eq $::ct_l_root} {
                ct_walk_result $past_boundary scope_boundary $path endpoint [ct_js $name] scope [ct_js $::ct_l_root] boundary_role [ct_js input]
                continue
            }
            if {$member_direction ni {npiOutput npiInout}} {
                if {!$::ct_l_internal && $member_owner ne "" && [dict get $member binding] ne ""} {
                    # The elaborated port HighConn preserves forwarding through
                    # a parent formal which npiActual may collapse.
                    set ref [npi_handle_by_name -name [dict get $member binding] -scope ""]
                    set parent_member [ct_l_interface_member $ref]
                    if {[dict size $parent_member] && [ct_l_interface_input [dict get $parent_member direction]]} {
                        set member_signal $ref
                    }
                }
                if {$member_signal eq ""} {
                    ct_walk_result $past_boundary unsupported_expression $path endpoint [ct_js $name] message [ct_js "Interface member binding could not be mapped to one RTL signal bit"]
                } else {
                    dict set queue $tail [list $member_signal $path $name $past_boundary]
                    incr tail; dict incr ::ct_walk_stats discovered
                }
                continue
            }
        }
        if {$check_formal} {
            lassign [ct_l_formal_input $h] input_scope formal
            if {$input_scope ne "" && [ct_is_input_boundary $input_scope $::ct_l_root]} {
                ct_walk_result $past_boundary [ct_input_status $input_scope] $path {*}[ct_boundary_fields $input_scope $formal]
                # This is the signal on the inside of the formal. It can also
                # have a local driver. ct_l_expand stops the root's port row
                # before its HighConn, while checking the other driver rows.
                set past_boundary 1
            }
        }
        if {!$past_boundary && $check_wires && $type in {npiNet npiNetBit npiBitSelect npiPartSelect npiIndexedPartSelect}} {
            lassign [ct_l_wire_boundary $h] wire_scope wire
            if {$wire_scope ne ""} {
                if {$name ne "$wire_scope.$wire"} {
                    set path [ct_l_step $path [ct_object type [ct_js npiNetBit] name [ct_js "$wire_scope.$wire"] net [ct_js ""]]]
                }
                ct_terminal [ct_result layer_wire_boundary $path {*}[ct_boundary_fields $wire_scope $wire driver boundary_wire]]
                set past_boundary 1
            }
        }
        if {![ct_l_transparent $h]} {ct_walk_result $past_boundary unsupported_expression $path endpoint [ct_js $name]; continue}
        if {$type in {npiOperation npiExtRef} || (![dict size $member] && [ct_l_size $h] != 1)} {ct_walk_result $past_boundary unsupported_expression $path endpoint [ct_js [::npi_L1::npi_expr_decompile $h]]; continue}
        if {[catch {ct_l_expand $h $member_signal} actions]} {ct_terminal [ct_result error $path message [ct_js $actions]]; continue}
        foreach action $actions {
            lassign $action kind value fields evidence past_port
            set next_past [expr {$past_boundary || $past_port eq "1"}]
            set step $path
            if {$evidence ne ""} {set step [ct_l_step $path $evidence]}
            if {$kind eq "terminal"} {
                ct_walk_result $next_past $value $step {*}$fields
            } elseif {$kind eq "l1_entry"} {
                if {!$next_past} {ct_emit [ct_object event [ct_js l1_entry] trace_id $trace_id result [ct_result $value $step {*}$fields]]}
            } else {
                if {$kind eq "next"} {set child [npi_handle_by_name -name $value -scope ""]} else {set child $value}
                if {$child eq ""} {ct_terminal [ct_result error $step message [ct_js "Cannot restore language signal $value"]]; continue}
                dict set queue $tail [list $child $step [expr {$name ne "" ? $name : $last_inside}] $next_past]
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
