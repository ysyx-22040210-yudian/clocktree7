# Narrow Language Model queries for auxiliary-register tracing. No Netlist Model.
# Requests are Tcl lists over an authenticated loopback socket, never eval'ed.
set env(CT7_LIBRARY_ONLY) 1
source clocktree-extract.tcl
source $env(CT7_CONFIG)
source $env(VERDI_HOME)/share/NPI/L1/TCL/npi_L1.tcl

proc cr_prop {h p} {
    if {$h eq ""} {return ""}
    return [npi_get_str -property $p -object $h]
}
proc cr_handle {h rel} {
    if {$h eq ""} {return ""}
    return [npi_handle -type $rel -refHandle $h]
}
proc cr_int {h p} {
    if {$h eq ""} {return -1}
    set value [npi_get -property $p -object $h]
    if {![string is integer -strict $value]} {return -1}
    return $value
}
proc cr_constant {h} {
    if {$h eq ""} {return ""}
    if {![cr_is_constant $h]} {return ""}
    # NPI evaluates literals/parameters and elaborated constant expressions;
    # it does not supply runtime values for ordinary signals.
    set value [npi_get_value -format npiDecStrVal -object $h]
    if {![regexp {^-?[0-9]+$} $value]} {return ""}
    return $value
}
proc cr_is_constant {h {depth 0}} {
    if {$h eq "" || $depth>128} {return 0}
    set type [cr_prop $h npiType]
    if {$type in {npiConstant npiParameter npiParameterBit npiGenVar}} {return 1}
    if {$type in {npiExtRef npiRefObj}} {return [cr_is_constant [cr_handle $h npiActual] [expr {$depth+1}]]}
    if {$type ne "npiOperation"} {return 0}
    set it [npi_iterate -type npiOperand -refHandle $h]
    if {$it eq ""} {return 0}
    set valid 1
    while {[set p [npi_scan -iterator $it]] ne ""} {if {![cr_is_constant $p [expr {$depth+1}]]} {set valid 0}}
    return $valid
}
proc cr_range {h} {
    set result {}
    set spec [cr_handle $h npiTypespec]
    set it ""
    if {$spec ne ""} {set it [npi_iterate -type npiRange -refHandle $spec]}
    if {$it ne ""} {
        while {[set range [npi_scan -iterator $it]] ne ""} {
            set l [cr_constant [cr_handle $range npiLeftRange]]
            set r [cr_constant [cr_handle $range npiRightRange]]
            lappend result [ct_strings [list $l $r]]
        }
    }
    return [ct_array $result]
}
proc cr_expr {h {depth 0}} {
    if {$h eq ""} {return null}
    if {$depth>128} {return [ct_object type [ct_js unsupported] reason [ct_js expression_depth]]}
    set type [cr_prop $h npiType]
    set width [cr_int $h npiSize]
    set fields [list type [ct_js $type] full [ct_js [cr_prop $h npiFullName]] name [ct_js [cr_prop $h npiName]] \
        width $width signed [expr {[cr_int $h npiSigned]==1 ? "true" : "false"}]]
    set text "";catch {set text [::npi_L1::npi_expr_decompile $h]}
    lappend fields text [ct_js $text]
    if {$type eq "npiOperation"} {
        lappend fields op [ct_js [cr_prop $h npiOpType]]
        set parts {};set it [npi_iterate -type npiOperand -refHandle $h]
        if {$it ne ""} {while {[set p [npi_scan -iterator $it]] ne ""} {lappend parts [cr_expr $p [expr {$depth+1}]]}}
        lappend fields args [ct_array $parts]
    } elseif {$type in {npiConstant npiParameter npiParameterBit npiGenVar}} {
        lappend fields value [ct_js [npi_get_value -format npiBinStrVal -object $h]]
    } elseif {$type in {npiExtRef npiRefObj}} {
        lappend fields actual [cr_expr [cr_handle $h npiActual] [expr {$depth+1}]]
    } elseif {$type in {npiPartSelect npiIndexedPartSelect npiBitSelect npiNetBit npiRegBit npiVarBit}} {
        lappend fields parent [cr_expr [cr_handle $h npiParent] [expr {$depth+1}]]
        if {$type eq "npiPartSelect"} {
            lappend fields left [ct_js [cr_constant [cr_handle $h npiLeftRange]]] right [ct_js [cr_constant [cr_handle $h npiRightRange]]]
        } elseif {$type eq "npiIndexedPartSelect"} {
            lappend fields base [cr_expr [cr_handle $h npiBaseExpr] [expr {$depth+1}]] \
                count [cr_expr [cr_handle $h npiWidthExpr] [expr {$depth+1}]] select [ct_js [cr_prop $h npiIndexedPartSelectType]]
        } else {
            set indices {}
            if {$type eq "npiBitSelect"} {lappend indices [ct_js [cr_constant [cr_handle $h npiIndex]]]} else {
                set it [npi_iterate -type npiIndex -refHandle $h]
                if {$it ne ""} {while {[set index [npi_scan -iterator $it]] ne ""} {lappend indices [ct_js [cr_constant $index]]}}
            }
            lappend fields indices [ct_array $indices]
        }
    } else {lappend fields ranges [cr_range $h]}
    return [ct_object {*}$fields]
}
proc cr_location {h} {
    return [list type [ct_js [cr_prop $h npiType]] file [ct_js [cr_prop $h npiFile]] line [cr_int $h npiLineNo]]
}
proc cr_ancestors {use} {
    set result {};set child $use;set h [cr_handle $child npiUpperStmt];set seen [dict create]
    while {$h ne "" && ![dict exists $seen $h]} {
        dict set seen $h 1
        set type [cr_prop $h npiType];set fields [cr_location $h]
        if {$type eq "npiAlways"} {lappend fields always [ct_js [cr_prop $h npiAlwaysType]]}
        if {$type in {npiIf npiIfElse npiEventControl npiCase npiWhile npiFor npiRepeat npiWait}} {
            lappend fields condition [cr_expr [cr_handle $h npiCondition]]
        }
        if {$type in {npiIf npiIfElse}} {
            set branch unknown;set yes [cr_handle $h npiStmt];set no [cr_handle $h npiElseStmt]
            if {$yes ne "" && [npi_compare_objects -object1 $yes -object2 $child]} {set branch true}
            if {$no ne "" && [npi_compare_objects -object1 $no -object2 $child]} {set branch false}
            lappend fields branch [ct_js $branch]
        }
        lappend result [ct_object {*}$fields]
        set child $h;set h [cr_handle $h npiUpperStmt]
    }
    return [ct_array $result]
}
proc cr_send {record} {puts $::cr_socket $record;flush $::cr_socket}
proc cr_port {instance pin} {
    set inst [npi_handle_by_name -name $instance -scope ""]
    if {$inst eq ""} {error "Instance not found: $instance"}
    set it [npi_iterate -type npiPort -refHandle $inst]
    if {$it ne ""} {
        while {[set p [npi_scan -iterator $it]] ne ""} {
            if {[cr_prop $p npiName] ne $pin} {continue}
            cr_send [ct_object event [ct_js port] name [ct_js $pin] direction [ct_js [cr_prop $p npiDirection]] \
                low [cr_expr [cr_handle $p npiLowConn]] high [cr_expr [cr_handle $p npiHighConn]]]
            return
        }
    }
    error "Port not found: $instance.$pin"
}
proc cr_query {name direction} {
    if {$direction ni {driver load}} {error "Invalid direction"}
    set h [npi_handle_by_name -name $name -scope ""]
    if {$h eq ""} {error "Signal not found: $name"}
    if {[cr_int $h npiSize] != 1} {error "Register tracing requires one elaborated bit: $name"}
    set rows {}
    ::npi_L1::npi_trace_${direction}_by_hdl2 $h rows 0 {} {1 0 1 0}
    foreach row $rows {
        lassign $row source scope pass uses use signals
        set fields [list event [ct_js row] scope [ct_js [cr_prop $scope npiFullName]] uses $uses \
            source [cr_expr $source] statement [ct_object {*}[cr_location $use]] ancestors [cr_ancestors $use]]
        set type [cr_prop $use npiType]
        if {$type eq "npiPort"} {
            lappend fields direction [ct_js [cr_prop $use npiDirection]] port [ct_js [cr_prop $use npiName]] \
                low [cr_expr [cr_handle $use npiLowConn]] high [cr_expr [cr_handle $use npiHighConn]]
        } elseif {$type in {npiContAssign npiAssignment}} {
            lappend fields lhs [cr_expr [cr_handle $use npiLhs]] rhs [cr_expr [cr_handle $use npiRhs]]
        } elseif {$type eq "npiPrimTerm"} {
            set primitive [cr_handle $use npiPrimitive]
            lappend fields primitive [ct_js [cr_prop $primitive npiPrimType]]
        }
        set parts {};foreach s $signals {lappend parts [cr_expr $s]}
        lappend fields signals [ct_array $parts]
        cr_send [ct_object {*}$fields]
    }
}

set cr_socket [socket 127.0.0.1 $env(CT7_REGISTER_PORT)]
fconfigure $cr_socket -encoding utf-8 -translation lf -buffering line
cr_send [ct_object event [ct_js hello] token [ct_js $env(CT7_REGISTER_TOKEN)]]
if {[catch {
    if {[info exists ct_elab] && $ct_elab ne ""} {
        ct_validate_compiler_log [file join [file dirname [file normalize $ct_elab]] elabcomLog compiler.log]
        debImport -elab $ct_elab
    } else {debImport -sv -f $ct_filelist -top $ct_top}
    ct_validate_compiler_log verdiLog/compiler.log
    cr_send [ct_object event [ct_js ready]]
    while {[gets $cr_socket request]>=0} {
        lassign $request operation first second
        if {$operation eq "quit"} {break}
        if {[catch {
            switch -- $operation {
                port {cr_port $first $second}
                query {cr_query $first $second}
                default {error "Unknown operation"}
            }
        } reason]} {cr_send [ct_object event [ct_js error] reason [ct_js $reason]]}
        npi_release_all_handles
        cr_send [ct_object event [ct_js end]]
    }
} reason]} {catch {cr_send [ct_object event [ct_js fatal] reason [ct_js $reason]]};puts stderr $::errorInfo}
catch {close $cr_socket}
debExit
