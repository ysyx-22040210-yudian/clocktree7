set env(CT7_LIBRARY_ONLY) 1
source clocktree-extract.tcl
source input_ports.tcl
source $env(CT7_CONFIG)
set ct_context 0
set ct_pending 0
set ct_stream [open $ct_output w]
fconfigure $ct_stream -encoding utf-8 -translation lf
if {[catch {
    puts CT7_INPUT_IMPORT_BEGIN; flush stdout
    if {[info exists ct_elab] && $ct_elab ne ""} {
        ct_validate_compiler_log [file join [file dirname [file normalize $ct_elab]] elabcomLog compiler.log]
        debImport -elab $ct_elab
    } else {debImport -sv -f $ct_filelist -top $ct_top}
    ct_validate_compiler_log verdiLog/compiler.log
    puts CT7_INPUT_IMPORT_END; flush stdout
    ct_discover_inputs
} message]} {
    puts stderr "CT7_INPUT_FAILED: $message\n$::errorInfo"
    catch {ct_emit [ct_object event [ct_js input_discovery_failed] reason [ct_js $message]]}
    catch {close $ct_stream}
}
debExit
