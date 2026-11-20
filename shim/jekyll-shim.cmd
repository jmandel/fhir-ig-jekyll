@echo off
rem Drop-in for `jekyll` on Windows: the IG Publisher runs `cmd /C <jekyll-command> build --destination "<out>"`.
rem Pass this file's full path as -jekyll. Heap may grow to 75% of RAM; no other sizing flags needed.
rem Finds shim.jar next to this file (release zip) or in build\ (source checkout).
set "SHIM_JAR=%~dp0shim.jar"
if not exist "%SHIM_JAR%" set "SHIM_JAR=%~dp0build\shim.jar"
java -XX:MaxRAMPercentage=75 %SHIM_JAVA_OPTS% -jar "%SHIM_JAR%" %*
