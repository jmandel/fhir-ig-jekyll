#!/bin/bash
# usage: clone.sh org/repo branch  -> repos/org__repo
dest="repos/${1/\//__}"
[ -d "$dest" ] && exit 0
GIT_TERMINAL_PROMPT=0 git clone -q --depth 1 --single-branch --branch "$2" --filter=blob:limit=2m "https://github.com/$1.git" "$dest" 2>/dev/null || echo "FAIL $1 $2"
