@echo off
rem Drop-in for `jekyll` on Windows: the IG Publisher runs `cmd /C <jekyll-command> build --destination "<out>"`.
rem Pass this file's full path as -jekyll. Heap may grow to 75% of RAM; no other sizing flags needed.
java -XX:MaxRAMPercentage=75 %SHIM_JAVA_OPTS% -jar "%~dp0build\shim.jar" %*
