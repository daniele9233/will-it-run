"""Terraform: configurations that are run (variables, locals, outputs and functions, with values the model never saw) and
configurations for a cloud that are only read (resources, arguments, the good practices that can be seen in the text).

The first kind is applied with the real ``terraform`` where it is installed, and the outputs are compared; without it the
same answer is judged on its structure and the report says so. Nothing here asks for a provider to be downloaded.
"""

from __future__ import annotations

from agentfit.arena.bank.kit import make
from agentfit.arena.model import Question

_HCL = "Rispondi con il solo codice, in un blocco ```hcl."
_RUN = ("La configurazione sarà eseguita con `terraform apply` senza provider né risorse cloud: usa solo variabili, valori locali e output. "
        "I valori delle variabili vengono forniti da fuori.")


def hcl(code: str) -> str:
    return f"```hcl\n{code.strip()}\n```"


def _run(n: int, title: str, ask: str, tfvars: dict, outputs: dict, reference: str, wrong: list[str], blocks: list[dict], must_have: list[str] | None = None,
         labels: dict[str, str] | None = None, difficulty: int = 2) -> Question:
    check = {"type": "terraform", "tfvars": tfvars, "outputs": outputs, "blocks": blocks, "must_have": must_have or [], "must_not": [], "labels": labels or {}}
    shown = "; ".join(f"output {k} = {v}" for k, v in outputs.items())
    return make("terraform", n, title, f"{ask}\n{_RUN}\n{_HCL}", check, shown, hcl(reference), wrong=[hcl(w) for w in wrong], max_tokens=1000, difficulty=difficulty)


def _read(n: int, title: str, ask: str, reference: str, wrong: list[str], blocks: list[dict], must_have: list[str], labels: dict[str, str], difficulty: int = 3,
          must_not: list[str] | None = None) -> Question:
    check = {"type": "terraform", "tfvars": {}, "outputs": {}, "blocks": blocks, "must_have": must_have, "must_not": must_not or [], "labels": labels}
    # What the checks look for, as a person would say it: the blocks that must be there when nothing else was given a name.
    shown = "; ".join(labels.values()) or "; ".join(" ".join([b["type"], *b.get("labels", [])]) for b in blocks)
    return make("terraform", n, title, f"{ask}\n{_HCL}", check, shown, hcl(reference), wrong=[hcl(w) for w in wrong], max_tokens=1400, difficulty=difficulty)


def build(seed: int) -> list[Question]:
    out: list[Question] = []

    out.append(_run(1, "Nomi in maiuscolo", "Scrivi una configurazione Terraform con la variabile `names` (lista di stringhe) e un output `upper` che contiene la stessa lista con ogni nome in maiuscolo.",
                    {"names": ["web", "api", "db"]}, {"upper": ["WEB", "API", "DB"]},
                    'variable "names" {\n  type = list(string)\n}\n\noutput "upper" {\n  value = [for n in var.names : upper(n)]\n}',
                    ['variable "names" {\n  type = list(string)\n  default = ["web", "api", "db"]\n}\n\noutput "upper" {\n  value = ["WEB", "API", "DB"]\n}', 'variable "names" {\n  type = list(string)\n}\n\noutput "upper" {\n  value = var.names\n}',
                     'variable "names" {\n  type = list(string)\n}\n\noutput "upper" {\n  value = upper(var.names)\n}'],
                    [{"type": "variable", "labels": ["names"]}, {"type": "output", "labels": ["upper"], "attrs": {"value": "var\\.names"}}], difficulty=1))

    out.append(_run(2, "Lunghezza dei nomi", "Scrivi una configurazione Terraform con la variabile `names` (lista di stringhe) e un output `lengths`: una mappa che associa a ogni nome la sua lunghezza in caratteri.",
                    {"names": ["alpha", "be", "gamma"]}, {"lengths": {"alpha": 5, "be": 2, "gamma": 5}},
                    'variable "names" {\n  type = list(string)\n}\n\noutput "lengths" {\n  value = { for n in var.names : n => length(n) }\n}',
                    ['variable "names" {\n  type = list(string)\n}\n\noutput "lengths" {\n  value = length(var.names)\n}', 'variable "names" {\n  type = list(string)\n}\n\noutput "lengths" {\n  value = { for n in var.names : n => n }\n}',
                     'output "lengths" {\n  value = { web = 3, api = 3 }\n}'],
                    [{"type": "variable", "labels": ["names"]}, {"type": "output", "labels": ["lengths"], "attrs": {"value": "\\blength\\s*\\("}}], difficulty=1))

    out.append(_run(3, "Etichetta dell'ambiente", "Scrivi una configurazione Terraform con la variabile `env` (stringa, valore predefinito \"dev\") e un output `label` che vale \"production\" se `env` è \"prod\", altrimenti \"non-production\".",
                    {"env": "prod"}, {"label": "production"},
                    'variable "env" {\n  type    = string\n  default = "dev"\n}\n\noutput "label" {\n  value = var.env == "prod" ? "production" : "non-production"\n}',
                    ['variable "env" {\n  type    = string\n  default = "dev"\n}\n\noutput "label" {\n  value = "non-production"\n}', 'variable "env" {\n  type    = string\n  default = "dev"\n}\n\noutput "label" {\n  value = var.env == "prod" ? "non-production" : "production"\n}',
                     'variable "env" {\n  type = string\n}\n\noutput "label" {\n  value = var.env\n}'],
                    [{"type": "variable", "labels": ["env"]}, {"type": "output", "labels": ["label"], "attrs": {"value": "\\bvar\\.env\\b"}}]))

    out.append(_run(4, "Tag con merge", "Scrivi una configurazione Terraform con le variabili `base_tags` (mappa di stringhe) ed `env` (stringa) e un output `tags` che contiene tutte le `base_tags` più la chiave `Environment` con il valore di `env`.",
                    {"base_tags": {"Owner": "team-a", "Project": "demo"}, "env": "prod"}, {"tags": {"Environment": "prod", "Owner": "team-a", "Project": "demo"}},
                    'variable "base_tags" {\n  type = map(string)\n}\n\nvariable "env" {\n  type = string\n}\n\noutput "tags" {\n  value = merge(var.base_tags, { Environment = var.env })\n}',
                    ['variable "base_tags" {\n  type = map(string)\n}\n\nvariable "env" {\n  type = string\n}\n\noutput "tags" {\n  value = var.base_tags\n}',
                     'variable "base_tags" {\n  type = map(string)\n}\n\nvariable "env" {\n  type = string\n}\n\noutput "tags" {\n  value = { Environment = var.env }\n}'],
                    [{"type": "variable", "labels": ["base_tags"]}, {"type": "variable", "labels": ["env"]}, {"type": "output", "labels": ["tags"], "attrs": {"value": "\\bmerge\\s*\\("}}]))

    out.append(_run(5, "Sottoreti con cidrsubnet", "Scrivi una configurazione Terraform con la variabile `vpc_cidr` (stringa) e un output `subnets`: la lista delle prime tre sottoreti /24 ricavate da `vpc_cidr` (che è un /16), "
                    "con indici da 0 a 2.", {"vpc_cidr": "10.20.0.0/16"}, {"subnets": ["10.20.0.0/24", "10.20.1.0/24", "10.20.2.0/24"]},
                    'variable "vpc_cidr" {\n  type = string\n}\n\noutput "subnets" {\n  value = [for i in range(3) : cidrsubnet(var.vpc_cidr, 8, i)]\n}',
                    ['variable "vpc_cidr" {\n  type = string\n}\n\noutput "subnets" {\n  value = cidrsubnet(var.vpc_cidr, 8, 1)\n}', 'output "subnets" {\n  value = ["10.0.0.0/24", "10.0.1.0/24", "10.0.2.0/24"]\n}',
                     'variable "vpc_cidr" {\n  type = string\n}\n\noutput "subnets" {\n  value = [for i in range(3) : cidrsubnet(var.vpc_cidr, 16, i)]\n}'],
                    [{"type": "variable", "labels": ["vpc_cidr"]}, {"type": "output", "labels": ["subnets"]}], must_have=["\\bcidrsubnet\\s*\\("], labels={"\\bcidrsubnet\\s*\\(": "the cidrsubnet function"}, difficulty=3))

    out.append(_run(6, "Variabile con validazione", "Scrivi una configurazione Terraform con la variabile `port` (numero, valore predefinito 8080) che accetta solo valori da 1 a 65535 (usa un blocco `validation` con un messaggio d'errore) "
                    "e un output `port_label` che vale la stringa \"port-\" seguita dal numero della porta.", {"port": 9090}, {"port_label": "port-9090"},
                    'variable "port" {\n  type    = number\n  default = 8080\n\n  validation {\n    condition     = var.port >= 1 && var.port <= 65535\n    error_message = "The port must be between 1 and 65535."\n  }\n}\n\noutput "port_label" {\n  value = "port-${var.port}"\n}',
                    ['variable "port" {\n  type    = number\n  default = 8080\n}\n\noutput "port_label" {\n  value = "port-${var.port}"\n}', 'variable "port" {\n  type    = number\n  default = 8080\n\n  validation {\n    condition     = var.port >= 1 && var.port <= 65535\n    error_message = "bad port"\n  }\n}\n\noutput "port_label" {\n  value = "port-var.port"\n}'],
                    [{"type": "variable", "labels": ["port"]}, {"type": "output", "labels": ["port_label"], "attrs": {"value": "\\bvar\\.port\\b"}}], must_have=["\\bvalidation\\s*\\{[^}]*\\bcondition\\b[^}]*\\berror_message\\b"], labels={"\\bvalidation\\s*\\{[^}]*\\bcondition\\b[^}]*\\berror_message\\b": "a validation block with a condition and a message"}))

    out.append(_run(7, "for_each su terraform_data", "Scrivi una configurazione Terraform con la variabile `envs` (insieme di stringhe), una risorsa `terraform_data` creata una volta per ogni elemento di `envs` con `for_each`, "
                    "e un output `envs` che contiene i nomi (le chiavi) delle istanze create, in ordine alfabetico.", {"envs": ["prod", "dev"]}, {"envs": ["dev", "prod"]},
                    'variable "envs" {\n  type = set(string)\n}\n\nresource "terraform_data" "env" {\n  for_each = var.envs\n  input    = each.key\n}\n\noutput "envs" {\n  value = sort(keys(terraform_data.env))\n}',
                    ['variable "envs" {\n  type = set(string)\n}\n\nresource "terraform_data" "env" {\n  count = length(var.envs)\n  input = "x"\n}\n\noutput "envs" {\n  value = sort(var.envs)\n}', 'variable "envs" {\n  type = set(string)\n}\n\noutput "envs" {\n  value = ["dev", "prod"]\n}'],
                    [{"type": "variable", "labels": ["envs"]}, {"type": "resource", "labels": ["terraform_data", "*"], "attrs": {"for_each": True}}, {"type": "output", "labels": ["envs"], "attrs": {"value": "\\bterraform_data\\.env\\b"}}], difficulty=3))

    s3_ref = ('variable "bucket_name" {\n  type = string\n}\n\nresource "aws_s3_bucket" "data" {\n  bucket = var.bucket_name\n}\n\nresource "aws_s3_bucket_versioning" "data" {\n  bucket = aws_s3_bucket.data.id\n\n  versioning_configuration {\n    status = "Enabled"\n  }\n}\n\n'
              'resource "aws_s3_bucket_public_access_block" "data" {\n  bucket                  = aws_s3_bucket.data.id\n  block_public_acls       = true\n  block_public_policy     = true\n  ignore_public_acls      = true\n  restrict_public_buckets = true\n}\n\n'
              'resource "aws_s3_bucket_server_side_encryption_configuration" "data" {\n  bucket = aws_s3_bucket.data.id\n\n  rule {\n    apply_server_side_encryption_by_default {\n      sse_algorithm = "AES256"\n    }\n  }\n}')
    out.append(_read(8, "Bucket S3 sicuro", "Scrivi una configurazione Terraform (provider AWS) per un bucket S3 privato: versioning attivo, blocco completo dell'accesso pubblico (tutte e quattro le opzioni) e cifratura lato server. "
                     "Il nome del bucket arriva dalla variabile `bucket_name`. Usa le risorse separate del provider AWS recente (non gli argomenti deprecati dentro `aws_s3_bucket`).", s3_ref,
                     [s3_ref.replace('resource "aws_s3_bucket" "data" {\n  bucket = var.bucket_name\n}', 'resource "aws_s3_bucket" "data" {\n  bucket = var.bucket_name\n  acl    = "public-read"\n}'),
                      'resource "aws_s3_bucket" "data" {\n  bucket = "my-bucket"\n\n  versioning {\n    enabled = true\n  }\n}',
                      s3_ref.replace("block_public_policy     = true", "block_public_policy     = false")],
                     [{"type": "variable", "labels": ["bucket_name"]}, {"type": "resource", "labels": ["aws_s3_bucket", "*"], "attrs": {"bucket": "var\\.bucket_name"}}, {"type": "resource", "labels": ["aws_s3_bucket_versioning", "*"]},
                      {"type": "resource", "labels": ["aws_s3_bucket_public_access_block", "*"]}, {"type": "resource", "labels": ["aws_s3_bucket_server_side_encryption_configuration", "*"]}],
                     ["\\bstatus\\s*=\\s*\"Enabled\"", "\\bblock_public_acls\\s*=\\s*true\\b", "\\bblock_public_policy\\s*=\\s*true\\b", "\\bignore_public_acls\\s*=\\s*true\\b", "\\brestrict_public_buckets\\s*=\\s*true\\b", "\\bsse_algorithm\\s*=\\s*\"(?:AES256|aws:kms)\"", ],
                     {"\\bstatus\\s*=\\s*\"Enabled\"": "versioning status Enabled", "\\bblock_public_acls\\s*=\\s*true\\b": "block_public_acls = true", "\\bblock_public_policy\\s*=\\s*true\\b": "block_public_policy = true",
                      "\\bignore_public_acls\\s*=\\s*true\\b": "ignore_public_acls = true", "\\brestrict_public_buckets\\s*=\\s*true\\b": "restrict_public_buckets = true", "\\bsse_algorithm\\s*=\\s*\"(?:AES256|aws:kms)\"": "a server-side encryption algorithm",
                      "\\bacl\\s*=\\s*\"public-read(?:-write)?\"": "a public ACL"},
                     must_not=["\\bacl\\s*=\\s*\"public-read(?:-write)?\""]))

    sg_ref = ('variable "vpc_id" {\n  type = string\n}\n\nvariable "ports" {\n  type = list(number)\n}\n\nresource "aws_security_group" "web" {\n  name   = "web"\n  vpc_id = var.vpc_id\n\n  dynamic "ingress" {\n    for_each = var.ports\n\n    content {\n      from_port   = ingress.value\n      to_port     = ingress.value\n      protocol    = "tcp"\n      cidr_blocks = ["0.0.0.0/0"]\n    }\n  }\n}')
    out.append(_read(9, "Security group con dynamic", "Scrivi una configurazione Terraform (provider AWS) con le variabili `vpc_id` (stringa) e `ports` (lista di numeri) e un `aws_security_group` chiamato `web` con una regola `ingress` TCP per ciascuna porta di `ports` "
                     "(da `from_port` a `to_port` uguali alla porta, da qualsiasi indirizzo), scritta con un blocco `dynamic` e non ripetendo i blocchi a mano.", sg_ref,
                     ['resource "aws_security_group" "web" {\n  name   = "web"\n  vpc_id = var.vpc_id\n\n  ingress {\n    from_port   = 80\n    to_port     = 80\n    protocol    = "tcp"\n    cidr_blocks = ["0.0.0.0/0"]\n  }\n\n  ingress {\n    from_port   = 443\n    to_port     = 443\n    protocol    = "tcp"\n    cidr_blocks = ["0.0.0.0/0"]\n  }\n}',
                      sg_ref.replace("for_each = var.ports", "for_each = var.vpc_id"), sg_ref.replace("ingress.value", "each.value")],
                     [{"type": "variable", "labels": ["vpc_id"]}, {"type": "variable", "labels": ["ports"]}, {"type": "resource", "labels": ["aws_security_group", "web"], "attrs": {"vpc_id": "var\\.vpc_id"}}],
                     ["\\bdynamic\\s+\"ingress\"", "\\bfor_each\\s*=\\s*var\\.ports\\b", "\\bfrom_port\\s*=\\s*ingress\\.value\\b", "\\bto_port\\s*=\\s*ingress\\.value\\b", "\\bprotocol\\s*=\\s*\"tcp\"", "\\bcidr_blocks\\b"],
                     {"\\bdynamic\\s+\"ingress\"": "a dynamic \"ingress\" block", "\\bfor_each\\s*=\\s*var\\.ports\\b": "for_each = var.ports", "\\bfrom_port\\s*=\\s*ingress\\.value\\b": "from_port = ingress.value",
                      "\\bto_port\\s*=\\s*ingress\\.value\\b": "to_port = ingress.value", "\\bprotocol\\s*=\\s*\"tcp\"": "protocol tcp", "\\bcidr_blocks\\b": "cidr_blocks"}))

    mod_ref = 'variable "vpc_cidr" {\n  type = string\n}\n\nmodule "vpc" {\n  source = "./modules/vpc"\n  cidr   = var.vpc_cidr\n  name   = "main"\n}\n\noutput "vpc_id" {\n  value = module.vpc.vpc_id\n}'
    out.append(_read(10, "Chiamare un modulo locale", "Scrivi il file principale di una configurazione Terraform che usa un modulo locale in `./modules/vpc` (il modulo accetta le variabili `cidr` e `name` e ha l'output `vpc_id`): "
                     "passa al modulo il CIDR dalla variabile `vpc_cidr` e il nome \"main\", ed esponi l'output del modulo come output `vpc_id`.", mod_ref,
                     [mod_ref.replace('source = "./modules/vpc"', 'source = "terraform-aws-modules/vpc/aws"'), mod_ref.replace("module.vpc.vpc_id", "var.vpc_cidr"), mod_ref.replace('  cidr   = var.vpc_cidr\n', "")],
                     [{"type": "variable", "labels": ["vpc_cidr"]}, {"type": "module", "labels": ["vpc"], "attrs": {"source": "\\./modules/vpc", "cidr": "var\\.vpc_cidr", "name": "\"main\""}}, {"type": "output", "labels": ["vpc_id"], "attrs": {"value": "module\\.vpc\\.vpc_id"}}],
                     [], {}, difficulty=2))
    return out
