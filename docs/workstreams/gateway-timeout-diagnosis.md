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
