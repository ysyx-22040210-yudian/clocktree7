# Read language-model formal inputs only. No driver, load or child traversal.
proc ct_input_ports {inst {direction npiInput}} {
    set result {}
    set it [npi_iterate -type npiPort -refHandle $inst]
    if {$it eq ""} {return $result}
    while {[set p [npi_scan -iterator $it]] ne ""} {
        if {[npi_get_str -property npiDirection -object $p] eq $direction} {
            set low [npi_handle -type npiLowConn -refHandle $p]
            set width [npi_get -property npiSize -object $p]
            set left ""; set right ""
            if {$low ne ""} {
                set width [npi_get -property npiSize -object $low]
                set range $low
                set typespec [npi_handle -type npiTypespec -refHandle $low]
                set range_it ""
                if {$typespec ne ""} {set range_it [npi_iterate -type npiRange -refHandle $typespec]}
                set ranges {}
                if {$range_it ne ""} {
                    while {[set r [npi_scan -iterator $range_it]] ne ""} {lappend ranges $r}
                    if {[llength $ranges]} {set range [lindex $ranges 0]}
                }
                foreach method {npiLeftRange npiRightRange} variable {left right} {
                    set h [npi_handle -type $method -refHandle $range]
                    if {$h ne ""} {
                        set $variable [npi_get_value -format npiDecStrVal -object $h]
                        npi_release_handle -object $h
                    }
                }
                foreach r $ranges {npi_release_handle -object $r}
                if {$typespec ne ""} {npi_release_handle -object $typespec}
                npi_release_handle -object $low
            }
            lappend result [list [npi_get_str -property npiName -object $p] $width $left $right]
        }
        npi_release_handle -object $p
    }
    return $result
}
proc ct_expand_subsystem {hdl requested {direction npiInput}} {
    set ports [dict create]
    foreach record [ct_input_ports $hdl $direction] {dict set ports [lindex $record 0] $record}
    set result {}
    foreach pin $requested {
        if {![dict exists $ports $pin]} {lappend result $pin; continue}
        lassign [dict get $ports $pin] name width left right
        if {$left ne "" && $right ne "" && [string is integer -strict $left] && [string is integer -strict $right]} {
            if {abs($left-$right)+1 != $width} {
                # Multidimensional packed arrays need explicit bit selections.
                # Never invent a flattened range from npiSize alone.
                lappend result $pin
                continue
            }
            set step [expr {$left <= $right ? 1 : -1}]
            for {set i $left} {1} {incr i $step} {
                lappend result "$pin\[$i\]"
                if {$i == $right} {break}
            }
        } else {lappend result $pin}
    }
    set unique {};set seen [dict create]
    foreach pin $result {if {![dict exists $seen $pin]} {dict set seen $pin 1;lappend unique $pin}}
    return $unique
}
proc ct_discover_inputs {} {
    global ct_rules ct_top ct_trace_scope
    set instances 0
    foreach rule $ct_rules {
        lassign $rule layer scope module inputs output kind
        if {$scope eq ""} {set scope [expr {$ct_trace_scope ne "" ? $ct_trace_scope : $ct_top}]}
        puts "CT7_INPUT_SCOPE $scope / $module"; flush stdout
        set root [npi_handle_by_name -name $scope -scope ""]
        if {$root eq ""} {error "Input discovery scope not found: $scope"}
        set handles {}
        ct_find_direct_instances $root $module handles
        npi_release_handle -object $root
        foreach h $handles {
            incr instances
            set path [npi_get_str -property npiFullName -object $h]
            puts "CT7_INPUT_INSTANCE $path"; flush stdout
            foreach record [ct_input_ports $h] {
                lassign $record name width left right
                ct_emit [ct_object event [ct_js input_port] instance [ct_js $path] module [ct_js $module] \
                    name [ct_js $name] direction [ct_js input] width $width left [ct_js $left] right [ct_js $right]]
            }
            flush $::ct_stream
            npi_release_handle -object $h
        }
    }
    ct_emit [ct_object event [ct_js input_discovery_complete] instances $instances]
    flush $::ct_stream
    ct_close_streams
    if {!$instances} {error "No matching direct child module; nested modules are not searched"}
}
