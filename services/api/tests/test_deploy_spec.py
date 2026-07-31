"""§18's one deployment control, asserted over the committed DigitalOcean spec.

DEVIATIONS 17.3. §18 names exactly one thing that must be true of a deploy:

    "Clerk outage during demo: `DEMO_BYPASS_AUTH=true` env flag renders a fixed
     parent session locally. **Never enabled in prod deploy.**"

The runtime half of that is already strong and already tested: `auth.py`'s
`assert_demo_bypass_is_local()` runs at module import AND again inside the grant
branch, and `tests/test_auth.py` drives every signal family. That half was
verified live for this audit rather than assumed — `GET /api/health` on the
hosted app returns `"demo_bypass_auth": false` and every response carries
`x-storyforge-auth: enforced`.

What had **no** check at all is the other half: the committed `.do/app.yaml`.
That file's own comment calls `ENVIRONMENT: production` a "§18 TRIPWIRE — do not
remove to make something work" and explains that `DEMO_BYPASS_AUTH` is
"deliberately ABSENT rather than set to \"false\"", because a variable already
listed in the dashboard is one toggle away from being true. Both statements were
enforced by nothing. `grep -r "app.yaml" services/api/tests apps/web/src` returned
nothing before this file.

Why it is worth a test rather than a comment: the tripwire's whole value is that
someone debugging a broken deploy at 3am cannot make the container start by
deleting one line. A comment saying "do not remove this" is removable; a red
suite is the thing that answers back. This is the same argument
`test_auth.py::test_the_bypass_refuses_to_start_in_anything_that_looks_deployed`
makes about `.env.example` — "a comment in `.env.example` is not a control".

Scope, stated so it is not overread: this asserts the SPEC IN THE REPO, not the
live application. If the app was created through the dashboard rather than from
this file, the two can differ and nothing here would know. That gap is named in
the audit report rather than papered over — and it is bounded by the runtime
guard, whose failure mode is a container that dies at import rather than one that
serves unauthenticated traffic.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from storyforge.auth import CONTAINER_OVERRIDE_VAR, is_deployed_env_value

APP_SPEC = Path(__file__).resolve().parents[3] / ".do" / "app.yaml"

#: Every flag that turns authentication off, by the name the env would carry.
#: `DEMO_BYPASS_ROLE` is not here — it is inert while the bypass is off — but
#: `CONTAINER_OVERRIDE_VAR` is, because it is the one variable that cancels the
#: single signal `docker run -e` cannot erase (`auth.py`, DEVIATIONS 8.66).
BYPASS_KEYS = frozenset({"DEMO_BYPASS_AUTH", CONTAINER_OVERRIDE_VAR})


def _components() -> list[tuple[str, dict]]:
    """Every deployable component in the spec, as `(label, component)`."""
    spec = yaml.safe_load(APP_SPEC.read_text(encoding="utf-8"))
    out = []
    for kind in ("services", "static_sites", "jobs", "workers", "functions"):
        for component in spec.get(kind) or []:
            out.append((f"{kind}/{component.get('name')}", component))
    return out


def _envs(component: dict) -> dict[str, str]:
    return {e["key"]: str(e.get("value", "")) for e in (component.get("envs") or [])}


def test_the_deploy_spec_is_readable_and_has_the_components_it_should():
    """The canary. Every assertion below is "no component does X", which a spec
    this test failed to parse would satisfy vacuously — the same empty-set
    failure `_UNENRICHED_SQL` had (11.99)."""
    labels = [label for label, _ in _components()]
    assert "services/api" in labels, f"no api service in {APP_SPEC}: found {labels}"
    assert "jobs/migrate" in labels, f"no migrate job in {APP_SPEC}: found {labels}"


def test_no_deployed_component_carries_an_auth_bypass_flag():
    """§18: "Never enabled in prod deploy" — including as a key set to "false".

    The spec's own comment gives the reason and it is not pedantry: `false` in
    the file becomes a row in the App Platform dashboard, and a row in the
    dashboard is one toggle and one redeploy away from `true`. Absent means the
    only way to turn it on is to add it, which is a diff someone reviews.
    """
    offenders = [
        (label, key, value)
        for label, component in _components()
        for key, value in _envs(component).items()
        if key in BYPASS_KEYS
    ]
    assert offenders == [], (
        f"{APP_SPEC} names an auth-bypass variable: {offenders}. §18 permits "
        "DEMO_BYPASS_AUTH on a laptop only, and it must be ABSENT here rather "
        "than present-and-false — `config.py` already defaults it to false."
    )


def test_every_server_side_component_declares_itself_deployed():
    """The tripwire, from the guard's own point of view.

    `auth.py::deployment_signals` refuses the bypass when it can see a signal;
    `ENVIRONMENT=production` is the one signal this repo controls rather than
    inherits from the platform, which is exactly why the spec sets it — DO's
    injected `APP_URL`/`DIGITALOCEAN_APP_ID` would also trip the guard, but those
    are a platform behaviour that could change and this one cannot.

    Asserted through `is_deployed_env_value`, the function the guard actually
    calls, rather than against the string "production": if the guard's notion of
    a deployed value ever narrows, this test moves with it instead of agreeing
    with a value the guard has stopped recognising.
    """
    for label, component in _components():
        if label.startswith("static_sites/"):
            continue  # no server, no auth, nothing to bypass
        value = _envs(component).get("ENVIRONMENT")
        assert value is not None, (
            f"{label} in {APP_SPEC} sets no ENVIRONMENT. It is the §18 tripwire: "
            "without it this component's refusal to run the auth bypass depends "
            "entirely on DigitalOcean continuing to inject APP_URL and PORT."
        )
        assert is_deployed_env_value(value.strip().lower()), (
            f"{label} sets ENVIRONMENT={value!r}, which "
            "`auth.deployment_signals` does NOT read as a deployed environment — "
            "so DEMO_BYPASS_AUTH=true would not be refused by this signal."
        )


# Shell metacharacters that only mean anything to a shell. App Platform starts a
# `run_command` in EXEC form, so a command containing any of these without an
# explicit interpreter is not "a command that does two things" — it is one
# argv whose [0] is a filename that does not exist.
_SHELL_ONLY = ("&&", "||", " | ", ";", ">", "<", "$(", "`")
_INTERPRETERS = ("sh ", "bash ", "/bin/sh", "/bin/bash", "python ", "python3 ")


def test_every_run_command_using_shell_syntax_actually_asks_for_a_shell():
    """DEVIATIONS 23.1. The migrate job read:

        run_command: cd /app/services/api && alembic upgrade head

    and had NEVER run. App Platform exec-forms a run_command, so it searched
    PATH for an executable named `cd` and exited 128 before alembic loaded:

        error finding executable "cd" in PATH: no such file or directory

    The comment beside it said "Idempotent: at head it is a no-op, verified
    locally" — and that was true. It was verified in a shell, and deployed
    without one. Local verification cannot see this class of defect at all,
    which is exactly why it belongs in the suite instead.

    What made it expensive rather than merely wrong: a PRE_DEPLOY failure
    aborts the deploy, and App Platform then performs an AUTOMATED ROLLBACK
    to the previous image. The rollback is itself a new deployment, and it
    reaches phase ACTIVE. So `doctl apps list-deployments` reports ACTIVE, the
    health endpoint answers 200, and the OLD code is serving. Two deploys were
    consumed this way before anyone read `cause`, which states it outright:
    "automated rollback after failed deployment of <id>". Green meant rolled
    back, and the deployed tag in the spec was the tag that had FAILED to
    deploy — the most misleading possible combination.

    This is the project's signature defect in its deployment config: a check
    (the ACTIVE phase) that passes because it asks the wrong question.
    """
    offenders = []
    for label, component in _components():
        cmd = (component.get("run_command") or "").strip()
        if not cmd:
            continue
        uses_shell_syntax = any(tok in cmd for tok in _SHELL_ONLY)
        asks_for_shell = cmd.startswith(_INTERPRETERS)
        if uses_shell_syntax and not asks_for_shell:
            offenders.append((label, cmd))
    assert not offenders, (
        "run_command uses shell syntax but App Platform will exec it without a "
        f"shell, so argv[0] is not an executable: {offenders}. "
        'Wrap it: sh -c "..."'
    )


def test_the_migrate_job_still_runs_alembic_at_all():
    """The control for the test above, which would be satisfied by a spec that
    simply had no migrate command. Deleting the migration step is a louder
    failure than mis-quoting it, and nothing else here would notice."""
    jobs = {label: c for label, c in _components() if label == "jobs/migrate"}
    assert jobs, "the migrate job vanished from the spec"
    cmd = (jobs["jobs/migrate"].get("run_command") or "").strip()
    assert "alembic upgrade head" in cmd, f"migrate job no longer migrates: {cmd!r}"
    assert "/app/services/api" in cmd, (
        "alembic.ini's prepend_sys_path is relative to the working directory, so "
        f"the job must still cd there: {cmd!r}"
    )
