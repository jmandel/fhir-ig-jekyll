#!/bin/bash
# usage: clone_tpl.sh org/repo [branch]
dest="templates/${1/\//__}${2:+@$2}"
[ -d "$dest" ] && exit 0
GIT_TERMINAL_PROMPT=0 git clone -q --depth 1 ${2:+--branch "$2"} "https://github.com/$1.git" "$dest" 2>/dev/null || echo "FAIL $1 $2"
