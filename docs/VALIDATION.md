# Validation record — 2026-10-08

Executed in the cloud checkout with Python 3.12.14 on Linux:

- `python -m unittest discover -s tests -v`: **22 tests passed**, zero failures,
  skips, or expected failures. Includes real child-process death after raw output,
  real child-process death after manifest creation, cycle recovery after death,
  and cross-process execution-lock contention.
- Source CLI demo and repeat demo: four tasks succeeded; execution count remained
  one before/after coordinator restart and subsequent invocations. The fixture
  recorded a synthetic zero-offset negative result. Artifacts copied under
  `examples/demo-artifacts/` include protocol, raw results and hashes.
- Built a wheel using `pip wheel --no-deps --no-build-isolation --no-cache-dir`;
  installed it under a temporary directory without dependencies or network and
  ran its full synthetic demo from outside the checkout successfully.
- Codex adapter tests used mocked subprocesses only: disabled gate, API-key-route
  refusal, ChatGPT routing, ephemeral/read-only context, and timeout group kill.
  No live model task or paid API call was made.

These checks validate local software behavior, not scientific results. Mac
execution, Study 3 connectivity, real study execution, model capabilities,
subscription/credit eligibility, and live Codex advisory calls were **not tested**.
Official developer-site pages were blocked by a proxy 403; OpenAI repository docs,
configuration schema, installed CLI help, and login status were inspected instead.

Fixtures explicitly use synthetic arithmetic; they do not implement the Study 3
protocol or support its affect/state-persistence hypothesis. Generated status may
contain cloud-local artifact paths, which are provenance for this capture, not Mac
paths. Generate fresh local state on the Mac.
