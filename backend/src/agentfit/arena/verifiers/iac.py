"""Infrastructure as code: Terraform configurations and Ansible playbooks.

A Terraform answer is read with a small HCL reader everywhere. Where Terraform is installed it is also *run*: ``fmt`` for the
syntax, and for a configuration that needs no provider (the model's own variables, locals and outputs) ``init``, ``validate``,
``apply`` and ``output``, with variable values the model never saw, so an output that was typed in by hand instead of
computed does not pass. A configuration that needs a provider is never applied: that would download plugins and talk to a
cloud. An Ansible answer is parsed as YAML and checked against what a playbook is made of; ansible-lint is used if it is there.
"""

from __future__ import annotations

import json
import re
import shlex
from typing import Any

import yaml

from agentfit.arena import hcl, sandbox
from agentfit.arena import text as tx
from agentfit.arena.model import FORMAT, WRONG, Answer, Check, Question, Reason, Verdict, why
from agentfit.arena.toolprobe import Tools
from agentfit.arena.verifiers import Context, register
from agentfit.arena.verifiers.basic import shorten
from agentfit.arena.verifiers.calling import _arg_problem
from agentfit.arena.verifiers.structured import _Compare, _show

# -- Terraform ---------------------------------------------------------------------------------------

_HCL_LANGS = {"hcl", "terraform", "tf", "tfvars", "hcl2"}
_TOP = re.compile(r"^\s*(?:terraform|provider|variable|locals|output|resource|data|module|moved|import|check)\b", re.MULTILINE)
_BUILTIN_RESOURCES = {"terraform_data"}


def extract_hcl(reply: str) -> str | None:
    blocks = [b.code for b in tx.code_blocks(reply) if b.lang in _HCL_LANGS]
    if not blocks:
        blocks = [b.code for b in tx.code_blocks(reply) if b.lang == "" and _TOP.search(b.code)]
    if blocks:
        return "\n".join(blocks)
    return reply if _TOP.search(reply) and "```" not in reply else None


def _forbidden(blocks: list[hcl.Block]) -> str | None:
    """What Terraform could do to this PC that the Arena will not let it: run commands, fetch from the network."""
    for block in hcl.walk(blocks):
        if block.type == "provisioner":
            return f'provisioner "{block.labels[0] if block.labels else ""}"'
        if block.type == "data" and block.labels and block.labels[0] in ("external", "http"):
            return f'data "{block.labels[0]}"'
    return None


def _providerless(blocks: list[hcl.Block]) -> bool:
    for block in blocks:
        if block.type in ("provider", "module", "import"):
            return False
        if block.type == "terraform" and any(b.type == "required_providers" for b in block.blocks):
            return False
        if block.type in ("resource", "data") and (block.type == "data" or not block.labels or block.labels[0] not in _BUILTIN_RESOURCES):
            return False
    return True


def _terraform_error(text: str) -> str:
    """The first error Terraform printed, with its box drawing taken off and its lines joined: what, where, and the detail."""
    plain = re.sub("[│╷╵╴╶]", " ", text)
    match = re.search(r"Error:\s*(.+)", plain, re.DOTALL)
    return shorten(match.group(1) if match else plain, 300)


def _diagnostics(raw: str) -> list[str]:
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    found = []
    for item in data.get("diagnostics", []):
        if item.get("severity") != "error":
            continue
        line = ((item.get("range") or {}).get("start") or {}).get("line")
        found.append(shorten(f"{item.get('summary', '')}: {item.get('detail', '')}".strip(": ") + (f" (line {line})" if line else ""), 220))
    return found


def _block_checks(spec: dict[str, Any], blocks: list[hcl.Block]) -> list[Check]:
    checks: list[Check] = []
    for wanted in spec.get("blocks", []):
        labels = wanted.get("labels", [])
        label = f"{wanted['type']} " + " ".join(f'"{x}"' for x in labels)
        candidates = [b for b in hcl.walk(blocks) if b.type == wanted["type"] and len(b.labels) >= len(labels)
                      and all(w == "*" or w == got for w, got in zip(labels, b.labels, strict=False))]
        if not candidates:
            checks.append(Check(False, why("There is no block {label}.", label=label.strip())))
            continue
        attrs = wanted.get("attrs", {})
        best_problems: list[str] | None = None
        for block in candidates:
            problems = []
            for name, pattern in attrs.items():
                value = block.attrs.get(name)
                if value is None:
                    problems.append(why("{label}: the attribute {name} is missing.", label=label.strip(), name=name))
                elif pattern is not True and re.search(pattern, value) is None:
                    problems.append(why("{label}: {name} is {value}, expected it to match {pattern}.", label=label.strip(), name=name, value=shorten(value, 60), pattern=pattern))
            if not problems:
                best_problems = []
                break
            if best_problems is None or len(problems) < len(best_problems):
                best_problems = problems  # type: ignore[assignment]
        if best_problems:
            checks.extend(Check(False, p) for p in best_problems)  # type: ignore[arg-type]
        else:
            checks.append(Check(True, why("The block {label} is there.", label=label.strip())))
    return checks


def _pattern_checks(spec: dict[str, Any], clean: str) -> list[Check]:
    checks: list[Check] = []
    labels = spec.get("labels", {})
    for pattern in spec.get("must_have", []):
        found = re.search(pattern, clean) is not None
        shown = labels.get(pattern) or pattern
        checks.append(Check(found, why("It uses {what}.", what=shown) if found else why("It does not use {what}.", what=shown)))
    for pattern in spec.get("must_not", []):
        found = re.search(pattern, clean) is not None
        shown = labels.get(pattern) or pattern
        checks.append(Check(not found, why("It avoids {what}.", what=shown) if not found else why("It uses {what}, which it should not.", what=shown)))
    return checks


def _result(checks: list[Check], method: str, shown: str, question: Question, hard: Reason | None) -> Verdict:
    good = sum(1 for c in checks if c.ok)
    if hard is not None:
        return Verdict.failed(WRONG, hard, checks=checks, extracted=shown, expected=question.expected, method=method)
    if not checks or good == len(checks):
        return Verdict.passed(checks=checks, extracted=shown, expected=question.expected, method=method)
    failing = [c for c in checks if not c.ok]
    reason = failing[0].reason if len(failing) == 1 else why("{n} of {total} requirements are not met.", n=len(failing), total=len(checks))
    if good == 0:
        return Verdict.failed(WRONG, reason, checks=checks, extracted=shown, expected=question.expected, method=method)
    return Verdict.partial(good / len(checks), reason, checks=checks, extracted=shown, expected=question.expected, method=method)


@register("terraform")
def terraform(spec: dict[str, Any], answer: Answer, question: Question, ctx: Context) -> Verdict:
    source = extract_hcl(answer.text)
    if source is None:
        return Verdict.failed(FORMAT, why("No Terraform code was found in the reply."), extracted=shorten(answer.text), expected=question.expected, method="structure", format_ok=False)
    shown = shorten(" ".join(source.split()), 400)
    try:
        blocks = hcl.parse(source)
    except hcl.HclError as exc:
        reason = why("The Terraform code has a syntax error: {error} (line {line}).", error=exc.message, line=exc.line)
        return Verdict.failed(WRONG, reason, checks=[Check(False, reason)], extracted=shown, expected=question.expected, method="structure")
    banned = _forbidden(blocks)
    if banned is not None:
        reason = why("It uses {what}, which the Arena never runs.", what=banned)
        return Verdict.failed(WRONG, reason, checks=[Check(False, reason)], extracted=shown, expected=question.expected, method="structure")
    checks = _block_checks(spec, blocks) + _pattern_checks(spec, hcl.blank(source, strings=False))
    tools: Tools = ctx.tools or Tools()
    binary = tools.terraform()
    if binary is None:
        return _result(checks, "structure", shown, question, None)
    workdir = sandbox.make_workdir(ctx.workdir)
    hard: Reason | None = None
    method = "terraform fmt + structure"
    try:
        (workdir / "main.tf").write_text(source, encoding="utf-8")
        env = sandbox.clean_env({"TF_IN_AUTOMATION": "1", "TF_INPUT": "0", "CHECKPOINT_DISABLE": "1", "TF_CLI_ARGS": "", "TF_LOG": ""})

        def tf(*args: str, timeout: float = 90) -> sandbox.RunResult:
            return sandbox.run_process([binary.path, *args], workdir, timeout=timeout, env=env, memory_mb=2048)

        fmt = tf("fmt", "-check", "-no-color", "-list=false")
        if fmt.returncode == 2 or fmt.timed_out:
            hard = why("terraform fmt cannot read the file: {error}", error=_terraform_error(fmt.stderr or fmt.stdout))
            checks.insert(0, Check(False, hard))
        else:
            checks.insert(0, Check(True, why("terraform fmt reads the file.")))
            outputs = spec.get("outputs") or {}
            if _providerless(blocks):
                method = "terraform validate" if not outputs else "terraform apply"
                (workdir / "terraform.tfvars.json").write_text(json.dumps(spec.get("tfvars") or {}), encoding="utf-8")
                init = tf("init", "-backend=false", "-input=false", "-no-color")
                if init.returncode != 0:
                    hard = why("terraform init failed: {error}", error=_terraform_error(init.stderr or init.stdout))
                    checks.insert(1, Check(False, hard))
                else:
                    validate = tf("validate", "-json", "-no-color")
                    problems = _diagnostics(validate.stdout)
                    if validate.returncode != 0 or problems:
                        hard = why("terraform validate says: {error}", error=problems[0] if problems else _terraform_error(validate.stderr or validate.stdout))
                        checks.insert(1, Check(False, hard))
                        checks.extend(Check(False, why("terraform validate says: {error}", error=p)) for p in problems[1:3])
                    else:
                        checks.insert(1, Check(True, why("terraform validate accepts the configuration.")))
                        if outputs:
                            applied = tf("apply", "-auto-approve", "-input=false", "-no-color")
                            if applied.returncode != 0:
                                hard = why("terraform apply failed: {error}", error=_terraform_error(applied.stderr or applied.stdout))
                                checks.insert(2, Check(False, hard))
                            else:
                                checks.insert(2, Check(True, why("terraform apply succeeds.")))
                                found, right = _compare_outputs(tf("output", "-json").stdout, outputs)
                                checks.extend(found)
                                if right == 0:
                                    # Not one of the outputs asked for is right: the configuration does not do what it was asked.
                                    hard = next(c.reason for c in found if not c.ok)
    finally:
        sandbox.remove_workdir(workdir)
    return _result(checks, method, shown, question, hard)


def _compare_outputs(raw: str, expected: dict[str, Any]) -> tuple[list[Check], int]:
    """A check for each wrong value, and for each output that is right; and how many outputs were right."""
    try:
        actual = json.loads(raw or "{}")
    except ValueError:
        actual = {}
    checks: list[Check] = []
    right = 0
    for name, value in expected.items():
        if name not in actual:
            checks.append(Check(False, why("The output {name} does not exist: expected {expected}.", name=name, expected=_show(value))))
            continue
        compare = _Compare(1e-9, False, False)
        compare.run(value, actual[name].get("value"), f"output {name}")
        checks.extend(compare.failures)
        if not compare.failures:
            right += 1
            checks.append(Check(True, why("The output {name} is {value}.", name=name, value=_show(actual[name].get("value")))))
    return checks, right


# -- Ansible -----------------------------------------------------------------------------------------------------

_TASK_KEYWORDS = {
    "name", "when", "loop", "register", "become", "become_user", "become_method", "become_flags", "vars", "tags", "notify", "listen", "changed_when",
    "failed_when", "ignore_errors", "ignore_unreachable", "delegate_to", "delegate_facts", "args", "environment", "no_log", "until", "retries", "delay",
    "block", "rescue", "always", "check_mode", "diff", "run_once", "any_errors_fatal", "throttle", "timeout", "connection", "remote_user", "port",
    "module_defaults", "collections", "async", "poll", "loop_control", "action", "local_action", "debugger", "ansible_check_mode",
}
_BUILTIN_MODULES = {
    "add_host", "apt", "apt_key", "apt_repository", "assemble", "assert", "async_status", "blockinfile", "command", "copy", "cron", "deb822_repository", "debconf",
    "debug", "dnf", "dnf5", "dpkg_selections", "expect", "fail", "fetch", "file", "find", "gather_facts", "get_url", "getent", "git", "group", "group_by", "hostname",
    "import_playbook", "import_role", "import_tasks", "include_role", "include_tasks", "include_vars", "iptables", "known_hosts", "lineinfile", "meta", "package",
    "package_facts", "pause", "ping", "pip", "raw", "reboot", "replace", "rpm_key", "script", "service", "service_facts", "set_fact", "set_stats", "setup", "shell",
    "slurp", "stat", "subversion", "systemd", "systemd_service", "tempfile", "template", "unarchive", "uri", "user", "validate_argument_spec", "wait_for",
    "wait_for_connection", "yum", "yum_repository",
}
_COLLECTIONS = ("ansible.posix.", "community.", "kubernetes.core.", "amazon.aws.", "google.cloud.", "azure.azcollection.", "containers.podman.", "ansible.windows.", "ansible.utils.")
_FREE_FORM = {"command", "shell", "raw", "script", "win_command", "win_shell"}
_YAML_LANGS = {"yaml", "yml"}


def _short(module: str) -> str:
    for prefix in ("ansible.builtin.", "ansible.legacy."):
        if module.startswith(prefix):
            return module[len(prefix):]
    return module


def _known(module: str) -> bool:
    return _short(module) in _BUILTIN_MODULES or (module.count(".") >= 2 and module.startswith(_COLLECTIONS))


def _task_module(task: dict[str, Any]) -> tuple[list[str], Any]:
    keys = [k for k in task if k not in _TASK_KEYWORDS and not str(k).startswith("with_")]
    if "action" in task and not keys:
        action = task["action"]
        if isinstance(action, dict):
            return [str(action.get("module", ""))], {k: v for k, v in action.items() if k != "module"}
        return [str(action).split()[0]], " ".join(str(action).split()[1:])
    return [str(k) for k in keys], task[keys[0]] if keys else None


def _module_args(module: str, value: Any, task: dict[str, Any]) -> dict[str, Any]:
    args: dict[str, Any] = {}
    if isinstance(value, dict):
        args.update(value)
    elif isinstance(value, str):
        if _short(module) in _FREE_FORM:
            args["cmd"] = value
        else:
            try:
                parts = shlex.split(value)
            except ValueError:
                parts = value.split()
            for part in parts:
                if "=" in part:
                    key, _eq, val = part.partition("=")
                    args[key] = val
    if isinstance(task.get("args"), dict):
        args.update(task["args"])
    for key, val in list(args.items()):
        if isinstance(val, str) and val.strip().lower() in ("yes", "true", "on"):
            args[key] = True
        elif isinstance(val, str) and val.strip().lower() in ("no", "false", "off"):
            args[key] = False
    return args


def _walk_tasks(items: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if "block" in item:
            for key in ("block", "rescue", "always"):
                out.extend(_walk_tasks(item.get(key) or []))
        else:
            out.append(item)
    return out


def _load_playbook(reply: str) -> tuple[Any, str | None, str]:
    """The YAML document of a reply, the error if it is not valid YAML, and the text it was read from."""
    blocks = [b.code for b in tx.code_blocks(reply) if b.lang in _YAML_LANGS]
    if not blocks:
        blocks = [b.code for b in tx.code_blocks(reply) if b.lang == ""]
    candidates = blocks or [reply]
    last_error: str | None = None
    for candidate in candidates:
        try:
            return yaml.safe_load(candidate), None, candidate
        except yaml.YAMLError as exc:
            last_error = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
            line = getattr(getattr(exc, "problem_mark", None), "line", None)
            if line is not None:
                last_error += f" (line {line + 1})"
    return None, last_error, candidates[-1]


@register("ansible")
def ansible(spec: dict[str, Any], answer: Answer, question: Question, ctx: Context) -> Verdict:
    document, error, source = _load_playbook(answer.text)
    shown = shorten(" ".join(answer.text.split()), 400)
    if error is not None:
        reason = why("The YAML is not valid: {error}", error=error)
        return Verdict.failed(WRONG, reason, checks=[Check(False, reason)], extracted=shown, expected=question.expected, method="structure")
    if not isinstance(document, (list, dict)):
        return Verdict.failed(FORMAT, why("No playbook was found in the reply."), extracted=shown, expected=question.expected, method="structure", format_ok=False)
    if spec.get("kind") == "tasks":
        plays: list[dict[str, Any]] = [{"tasks": document}] if isinstance(document, list) else []
    else:
        plays = [p for p in document if isinstance(p, dict)] if isinstance(document, list) else []
    if not plays:
        reason = why("A playbook is a list of plays, each with hosts and tasks.")
        return Verdict.failed(WRONG, reason, checks=[Check(False, reason)], extracted=shown, expected=question.expected, method="structure")
    checks: list[Check] = [Check(True, why("The YAML is valid."))]
    tasks: list[tuple[dict[str, Any], dict[str, Any]]] = []
    handlers: list[dict[str, Any]] = []
    for play in plays:
        if spec.get("kind") != "tasks" and "hosts" not in play and "import_playbook" not in play:
            checks.append(Check(False, why("A play has no hosts.")))
        for section in ("pre_tasks", "tasks", "post_tasks"):
            tasks.extend((play, t) for t in _walk_tasks(play.get(section) or []))
        handlers.extend(_walk_tasks(play.get("handlers") or []))
    if not tasks:
        checks.append(Check(False, why("The playbook has no tasks.")))
    structure_ok = True
    for _play, task in tasks + [({}, h) for h in handlers]:
        modules, _value = _task_module(task)
        label = str(task.get("name") or (modules[0] if modules else "?"))
        if len(modules) != 1:
            structure_ok = False
            checks.append(Check(False, why("The task {name} has {n} modules, it needs exactly one.", name=label, n=len(modules)) if modules else why("The task {name} has no module.", name=label)))
        elif not _known(modules[0]):
            structure_ok = False
            checks.append(Check(False, why("The module {module} does not exist.", module=modules[0])))
    for value in _strings(document):
        if value.count("{{") != value.count("}}") or value.count("{%") != value.count("%}"):
            structure_ok = False
            checks.append(Check(False, why("A Jinja expression is not closed: {text}", text=shorten(value, 60))))
            break
    if structure_ok and tasks:
        checks.append(Check(True, why("Every task has one known module.")))
    if spec.get("names"):
        unnamed = [t for _p, t in tasks if not t.get("name")]
        checks.append(Check(not unnamed, why("Every task has a name.") if not unnamed else why("{n} tasks have no name.", n=len(unnamed))))
    for wanted in spec.get("tasks", []):
        checks.extend(_expect_task(wanted, tasks))
    checks.extend(_pattern_checks(spec, re.sub(r"(?m)^\s*#.*$", "", source)))
    for key, value in (spec.get("keywords") or {}).items():
        everywhere = any(p.get(key) == value for p in plays) or (bool(tasks) and all(t.get(key) == value or p.get(key) == value for p, t in tasks))
        checks.append(Check(everywhere, why("{key} is {value}.", key=key, value=json.dumps(value)) if everywhere else why("{key}: {value} is missing.", key=key, value=json.dumps(value))))
    if spec.get("handlers"):
        known = {str(h.get("name")) for h in handlers} | {str(x) for h in handlers for x in (h.get("listen") if isinstance(h.get("listen"), list) else [h.get("listen")]) if x}
        for _p, task in tasks:
            notified = task.get("notify")
            for name in [notified] if isinstance(notified, str) else (notified or []):
                checks.append(Check(str(name) in known, why("The handler {name} exists.", name=name) if str(name) in known else why("The handler {name} is notified but never defined.", name=name)))
    lint = ctx.tools.ansible_lint() if ctx.tools else Tools().ansible_lint()
    method = "structure"
    if lint is not None:
        method = "structure + ansible-lint"
        checks.extend(_lint(lint.path, answer.text))
    good = sum(1 for c in checks if c.ok)
    if good == len(checks):
        return Verdict.passed(checks=checks, extracted=shown, expected=question.expected, method=method)
    failing = [c for c in checks if not c.ok]
    reason = failing[0].reason if len(failing) == 1 else why("{n} of {total} requirements are not met.", n=len(failing), total=len(checks))
    if good <= 1:
        return Verdict.failed(WRONG, reason, checks=checks, extracted=shown, expected=question.expected, method=method)
    return Verdict.partial(good / len(checks), reason, checks=checks, extracted=shown, expected=question.expected, method=method)


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def _expect_task(wanted: dict[str, Any], tasks: list[tuple[dict[str, Any], dict[str, Any]]]) -> list[Check]:
    names = [m.lower() for m in wanted["module"]]
    label = "/".join(wanted["module"][:3])
    best: list[Reason] | None = None
    for play, task in tasks:
        modules, value = _task_module(task)
        if not modules or _short(modules[0]).lower() not in names:
            continue
        args = _module_args(modules[0], value, task)
        problems: list[Reason] = []
        for arg, rule in (wanted.get("args") or {}).items():
            if arg not in args:
                problems.append(why("{module}: argument {arg} is missing.", module=_short(modules[0]), arg=arg))
                continue
            problem = _arg_problem(_short(modules[0]), arg, rule, args[arg])
            if problem is not None:
                problems.append(problem)
        for key, needed in (wanted.get("keywords") or {}).items():
            have = task.get(key) if key in task else play.get(key)
            if needed is True and not have:
                problems.append(why("{module}: {key} is missing.", module=_short(modules[0]), key=key))
            elif needed is not True and have != needed:
                problems.append(why("{module}: {key} should be {value}.", module=_short(modules[0]), key=key, value=json.dumps(needed)))
        if not problems:
            return [Check(True, why("There is a {module} task as asked.", module=label))]
        if best is None or len(problems) < len(best):
            best = problems
    if best is None:
        return [Check(False, why("There is no task using the module {module}.", module=label))]
    return [Check(False, p) for p in best]


def _lint(path: str, reply: str) -> list[Check]:
    blocks = [b.code for b in tx.code_blocks(reply) if b.lang in _YAML_LANGS] or [reply]
    workdir = sandbox.make_workdir()
    try:
        (workdir / "playbook.yml").write_text(blocks[0], encoding="utf-8")
        result = sandbox.run_process([path, "--nocolor", "-f", "json", "--profile", "min", "playbook.yml"], workdir, timeout=60, env=sandbox.clean_env())
    finally:
        sandbox.remove_workdir(workdir)
    if result.timed_out:
        return [Check(False, why("ansible-lint did not finish."))]
    try:
        items = json.loads(result.stdout or "[]")
    except ValueError:
        return []
    checks = []
    for item in items if isinstance(items, list) else []:
        rule = item.get("check_name") or (item.get("rule") or {}).get("id") or "rule"
        description = item.get("description") or (item.get("rule") or {}).get("description") or ""
        lines = (item.get("location") or {}).get("lines") or {}
        line = lines.get("begin", {}).get("line") if isinstance(lines.get("begin"), dict) else lines.get("begin")
        checks.append(Check(False, why("ansible-lint {rule}: {description}", rule=rule, description=shorten(str(description) + (f" (line {line})" if line else ""), 120))))
    if not checks:
        checks.append(Check(True, why("ansible-lint finds nothing wrong.")))
    return checks
