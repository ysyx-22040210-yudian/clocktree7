#!/usr/bin/env bash
# Source this file; it contains installation paths, never license contents.
export VERDI_HOME="${VERDI_HOME:-/home/synopsys/verdi/Verdi_O-2018.09-SP2}"
export VCS_HOME="${VCS_HOME:-/home/synopsys/vcs/O-2018.09-SP2}"
export VCS_TARGET_ARCH="${VCS_TARGET_ARCH:-linux64}"
export LM_LICENSE_FILE="${LM_LICENSE_FILE:-27000@IC_EDA}"
export SNPSLMD_LICENSE_FILE="${SNPSLMD_LICENSE_FILE:-$LM_LICENSE_FILE}"
export PATH="$VCS_HOME/bin:$VERDI_HOME/bin:$PATH"
