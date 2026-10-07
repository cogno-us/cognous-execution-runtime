# Gateway timeout diagnosis

Based on Worker 21 executor head ba0beb714064a225e3def69bb53ee388439ee43e.
Bun job 112900082483 and macOS job 112917022772 both timed out in the first
HTTP tool invocation test after 120 seconds. Windows job 112908208861 hit its
12-minute batch limit without a reported assertion failure.

This diagnostic branch adds phase logging only and runs the existing nine-test
file separately on Linux/Bun, macOS/Node and Windows/Node. Assertions and the
existing test timeout are unchanged. The test step is bounded to four minutes.
No runtime fix, failure cause or passing result is claimed yet. Full Worker 21
qualification and accepted hub pins remain unchanged. Local live execution is
unavailable because the environment denies network interface enumeration.

## Fixture repair under qualification

The HTTP test fixture installs a plugin registry, but real gateway startup calls
the production plugin loader, which discovers/imports plugins and replaces the
active registry. The scoped test now supplies its registry at that loader boundary.
The actual HTTP server, authentication, tool execution and policy checks remain
real. A control request now proves the fixture plugin HTTP handler is installed,
while the tool route must still take precedence. Production loader code and its
separate tests are unchanged. Whether this removes the measured startup delay
is pending the focused three-platform run; no timeout was increased.

Scoped lint: zero warnings/errors. Local live tests remain unavailable due to
network interface enumeration restrictions. Existing assertions are preserved.

## Independent OS workflows

The matrix workflow is replaced by gateway-test-linux.yml, gateway-test-macos.yml
and gateway-test-windows.yml. Each has its own result, concurrency group and logs;
there are no dependencies between OS jobs. Each runs only the gateway HTTP file
with one worker and a four-minute test-step limit. These are focused tests, not
full-repository CI. Linux retains Bun; macOS and Windows retain Node.

At head cae5603e856511e9bf36de23764472120f5ff00e, run 37665832785 reported
Linux success, macOS success and Windows failure. The split does not reclassify
that failure or claim a Windows repair. Source assertions and limits are unchanged.
