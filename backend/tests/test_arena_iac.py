"""Terraform and Ansible answers: parsed structurally everywhere, and run for real where the tool is installed."""

from __future__ import annotations

import os
import shutil

import pytest

from agentfit.arena import hcl
from agentfit.arena.model import Answer, Question
from agentfit.arena.toolprobe import Tools
from agentfit.arena.verifiers import Context, verify

HAS_TERRAFORM = shutil.which("terraform") is not None
NO_TOOLS = Tools(overrides={"terraform": None, "ansible_lint": None})


def q(check: dict, category: str = "terraform") -> Question:
    return Question(id="i.001", category=category, title="t", lang="en", prompt="p", check=check, expected="", reference="")


def run(check: dict, text: str, tools: Tools | None = None, category: str = "terraform", finish: str = "stop"):
    return verify(q(check, category), Answer(text=text, raw=text, finish_reason=finish), Context(tools=tools or NO_TOOLS))


def hclblock(code: str) -> str:
    return f"```hcl\n{code}\n```"


# -- the HCL reader --------------------------------------------------------------------------------------

SAMPLE = '''
terraform {
  required_version = ">= 1.5"
}

# a comment with { braces
variable "names" {
  type    = list(string)
  default = ["web", "api"]   // trailing comment
}

locals {
  upper = [for n in var.names : upper(n)]
  tags  = { for n in var.names : n => "${n}-${var.env != "" ? var.env : "dev"}" }
  doc   = <<-EOT
    a heredoc with } and "quotes"
  EOT
}

resource "aws_s3_bucket" "logs" {
  bucket = "logs-${var.env}"
  /* block comment */
  versioning {
    enabled = true
  }
}

output "upper" {
  value = local.upper
}
'''


def test_the_reader_finds_blocks_labels_attributes_and_nested_blocks():
    blocks = hcl.parse(SAMPLE)
    assert [(b.type, b.labels) for b in blocks] == [
        ("terraform", []), ("variable", ["names"]), ("locals", []), ("resource", ["aws_s3_bucket", "logs"]), ("output", ["upper"]),
    ]
    variable = blocks[1]
    assert variable.attrs["type"] == "list(string)" and variable.attrs["default"] == '["web", "api"]'
    assert "tags" in blocks[2].attrs and blocks[2].attrs["upper"].startswith("[for n in var.names")
    resource = blocks[3]
    assert resource.attrs["bucket"] == '"logs-${var.env}"'
    assert [(b.type, b.attrs) for b in resource.blocks] == [("versioning", {"enabled": "true"})]


def test_unbalanced_braces_and_stray_text_are_syntax_errors_with_a_line_number():
    with pytest.raises(hcl.HclError) as missing:
        hcl.parse('variable "x" {\n  type = string\n')
    assert missing.value.line >= 1
    with pytest.raises(hcl.HclError) as stray:
        hcl.parse('variable "x" {\n  type = string\n}\nthis is not hcl\n')
    assert stray.value.line == 4
    with pytest.raises(hcl.HclError):
        hcl.parse('output "x" {\n  value = "unterminated\n}\n')


def test_blanking_hides_comments_and_strings_for_the_forbidden_word_scan():
    cleaned = hcl.blank(SAMPLE)
    assert "a comment" not in cleaned and "heredoc with" not in cleaned
    assert "variable" in cleaned and len(cleaned) == len(SAMPLE)


# -- without the Terraform program ------------------------------------------------------------------------

STRUCT = {
    "type": "terraform", "outputs": {"upper": ["WEB", "API"]}, "tfvars": {"names": ["web", "api"]},
    "must_have": ["\\bvariable\\s+\"names\"", "\\bfor\\s+\\w+\\s+in\\s+var\\.names\\b", "\\bupper\\s*\\("],
    "blocks": [{"type": "output", "labels": ["upper"]}],
}
GOOD_TF = '''variable "names" {
  type = list(string)
}

output "upper" {
  value = [for n in var.names : upper(n)]
}
'''


def test_without_terraform_the_answer_is_judged_by_its_structure_and_says_so():
    v = run(STRUCT, hclblock(GOOD_TF))
    assert v.status == "passed" and v.method == "structure"


def test_a_missing_construct_fails_a_check_and_a_syntax_error_fails_the_question():
    no_for = GOOD_TF.replace("[for n in var.names : upper(n)]", "var.names")
    v = run(STRUCT, hclblock(no_for))
    assert v.status == "partial" and any(not c.ok for c in v.checks)
    broken = run(STRUCT, hclblock('output "upper" {\n  value = var.names\n'))
    assert (broken.status, broken.kind) == ("failed", "wrong") and "line" in broken.reason.render().lower()
    assert run(STRUCT, "Non so come si scrive.").kind == "format"


def test_a_provisioner_is_never_run_and_is_said_to_be_the_reason():
    evil = GOOD_TF + '\nresource "terraform_data" "x" {\n  provisioner "local-exec" {\n    command = "echo hi"\n  }\n}\n'
    v = run(STRUCT, hclblock(evil))
    assert v.status == "failed" and "local-exec" in v.reason.render() + " ".join(c.reason.render() for c in v.checks)


def test_blocks_can_be_required_with_attributes_that_match_a_pattern():
    spec = {"type": "terraform", "blocks": [{"type": "resource", "labels": ["aws_s3_bucket", "*"], "attrs": {"bucket": "var\\.name"}}]}
    ok = hclblock('resource "aws_s3_bucket" "b" {\n  bucket = var.name\n}\n')
    assert run(spec, ok).status == "passed"
    wrong = hclblock('resource "aws_s3_bucket" "b" {\n  bucket = "fixed"\n}\n')
    v = run(spec, wrong)
    assert v.status != "passed" and any("bucket" in c.reason.render() for c in v.checks if not c.ok)
    assert run(spec, hclblock('resource "aws_instance" "b" {\n}\n')).status != "passed"


# -- with it ------------------------------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_TERRAFORM, reason="terraform is not installed")
class TestWithTerraform:
    tools = Tools()

    def test_a_correct_configuration_is_applied_and_its_outputs_compared(self):
        v = run(STRUCT, hclblock(GOOD_TF), tools=self.tools)
        assert v.status == "passed", [c.reason.render() for c in v.checks if not c.ok]
        assert v.method == "terraform apply"

    def test_hardcoded_values_are_caught_because_the_variables_are_set_at_apply_time(self):
        hardcoded = 'output "upper" {\n  value = ["WEB", "API"]\n}\nvariable "names" {\n  type = list(string)\n  default = ["web", "api"]\n}\n'
        spec = {**STRUCT, "tfvars": {"names": ["db", "cache"]}, "outputs": {"upper": ["DB", "CACHE"]}, "must_have": []}
        v = run(spec, hclblock(hardcoded), tools=self.tools)
        assert v.status == "failed"
        failing = " ".join(c.reason.render() for c in v.checks if not c.ok)
        assert "upper" in failing and "WEB" in failing and "DB" in failing

    def test_an_error_from_terraform_is_quoted(self):
        bad = GOOD_TF.replace("upper(n)", "uppercase(n)")
        v = run({**STRUCT, "must_have": []}, hclblock(bad), tools=self.tools)
        assert v.status == "failed"
        assert "uppercase" in v.reason.render() + " ".join(c.reason.render() for c in v.checks)

    def test_a_syntax_error_is_caught_by_terraform_itself(self):
        v = run({**STRUCT, "must_have": [], "blocks": []}, hclblock('output "upper" {\n  value = [1, 2\n}\n'), tools=self.tools)
        assert v.status == "failed"

    def test_a_configuration_with_a_provider_is_only_formatted_and_read(self):
        spec = {"type": "terraform", "blocks": [{"type": "resource", "labels": ["aws_s3_bucket", "*"]}]}
        code = 'terraform {\n  required_providers {\n    aws = { source = "hashicorp/aws" }\n  }\n}\nresource "aws_s3_bucket" "b" {\n  bucket = "x"\n}\n'
        v = run(spec, hclblock(code), tools=self.tools)
        assert v.status == "passed" and v.method == "terraform fmt + structure"

    def test_a_provisioner_is_not_run_even_with_terraform_installed(self):
        evil = 'resource "terraform_data" "x" {\n  provisioner "local-exec" {\n    command = "echo hi > pwned.txt"\n  }\n}\n'
        v = run({"type": "terraform", "outputs": {}}, hclblock(evil), tools=self.tools)
        assert v.status == "failed"


# -- Ansible --------------------------------------------------------------------------------------------------

PLAY = {
    "type": "ansible",
    "tasks": [
        {"module": ["package", "apt", "dnf", "yum"], "args": {"name": {"eq": "nginx"}, "state": {"in": ["present", "latest"]}}},
        {"module": ["service", "systemd"], "args": {"name": {"eq": "nginx"}, "state": {"eq": "started"}, "enabled": {"eq": True}}},
    ],
    "keywords": {"become": True},
}
GOOD_PLAY = """```yaml
- name: Install and start nginx
  hosts: web
  become: true
  tasks:
    - name: Install nginx
      ansible.builtin.apt:
        name: nginx
        state: present
        update_cache: yes
    - name: Start nginx
      ansible.builtin.service:
        name: nginx
        state: started
        enabled: yes
```"""


def test_a_playbook_with_the_right_tasks_and_arguments_passes():
    v = run(PLAY, GOOD_PLAY, category="ansible")
    assert v.status == "passed", [c.reason.render() for c in v.checks if not c.ok]
    assert v.method == "structure"


def test_short_module_names_and_the_old_key_value_style_are_understood():
    old = """```yaml
- hosts: web
  become: yes
  tasks:
    - name: Install nginx
      apt: name=nginx state=latest
    - name: Start nginx
      service: name=nginx state=started enabled=yes
```"""
    assert run(PLAY, old, category="ansible").status == "passed"


def test_a_shell_command_instead_of_a_module_does_not_count():
    shelly = GOOD_PLAY.replace("ansible.builtin.apt:\n        name: nginx\n        state: present\n        update_cache: yes", "ansible.builtin.shell: apt-get install -y nginx")
    v = run(PLAY, shelly, category="ansible")
    assert v.status == "partial" and any(not c.ok and "package" in c.reason.render() for c in v.checks)


def test_wrong_arguments_and_a_missing_become_are_named():
    wrong = GOOD_PLAY.replace("state: started", "state: stopped").replace("  become: true\n", "")
    v = run(PLAY, wrong, category="ansible")
    failing = " | ".join(c.reason.render() for c in v.checks if not c.ok)
    assert "state" in failing and "become" in failing


def test_invalid_yaml_and_the_wrong_shape_fail_the_question():
    broken = run(PLAY, "```yaml\n- hosts: web\n  tasks:\n    - name: x\n      apt: {name: nginx\n```", category="ansible")
    assert broken.status == "failed" and "YAML" in broken.reason.render()
    not_a_playbook = run(PLAY, "```yaml\nname: nginx\nstate: present\n```", category="ansible")
    assert not_a_playbook.status == "failed"
    assert run(PLAY, "Si usa apt.", category="ansible").kind == "format"


def test_a_task_with_two_modules_or_an_unknown_module_is_a_structure_error():
    two = "```yaml\n- hosts: all\n  tasks:\n    - name: both\n      apt: name=nginx\n      service: name=nginx\n```"
    v = run({"type": "ansible", "tasks": []}, two, category="ansible")
    assert v.status != "passed" and any(not c.ok and "module" in c.reason.render() for c in v.checks)
    ghost = "```yaml\n- hosts: all\n  tasks:\n    - name: x\n      install_package: name=nginx\n```"
    v = run({"type": "ansible", "tasks": []}, ghost, category="ansible")
    assert v.status != "passed" and any(not c.ok and "install_package" in c.reason.render() for c in v.checks)


def test_a_handler_that_a_task_notifies_must_exist():
    spec = {"type": "ansible", "tasks": [{"module": ["template"], "args": {"dest": {"eq": "/etc/nginx/nginx.conf"}}}], "handlers": True}
    ok = """```yaml
- hosts: web
  tasks:
    - name: Configure
      ansible.builtin.template:
        src: nginx.conf.j2
        dest: /etc/nginx/nginx.conf
      notify: Restart nginx
  handlers:
    - name: Restart nginx
      ansible.builtin.service:
        name: nginx
        state: restarted
```"""
    assert run(spec, ok, category="ansible").status == "passed"
    missing = ok.split("  handlers:")[0] + "```"
    v = run(spec, missing, category="ansible")
    assert v.status != "passed" and any(not c.ok and "Restart nginx" in c.reason.render() for c in v.checks)


def test_unquoted_jinja_at_the_start_of_a_value_is_the_classic_yaml_mistake():
    bad = "```yaml\n- hosts: all\n  vars:\n    pkg: nginx\n  tasks:\n    - name: x\n      apt:\n        name: {{ pkg }}\n```"
    v = run({"type": "ansible", "tasks": []}, bad, category="ansible")
    assert v.status == "failed" and "YAML" in v.reason.render()


@pytest.mark.skipif(os.name != "nt", reason="the stand-in for ansible-lint is a Windows batch file")
def test_ansible_lint_is_used_when_it_is_installed(tmp_path):
    from agentfit.arena.toolprobe import Tool

    script = tmp_path / "ansible-lint.cmd"
    script.write_text('@echo off\r\necho [{"rule": {"id": "yaml[truthy]", "description": "Truthy value should be one of false, true"}, "location": {"lines": {"begin": {"line": 4}}}}]\r\nexit /b 2\r\n', encoding="utf-8")
    tools = Tools(overrides={"ansible_lint": Tool("ansible_lint", str(script), "24.0")})
    v = run(PLAY, GOOD_PLAY, tools=tools, category="ansible")
    assert v.status == "partial"
    assert any(not c.ok and "yaml[truthy]" in c.reason.render() for c in v.checks)
