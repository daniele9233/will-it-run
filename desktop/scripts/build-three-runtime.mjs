import { build } from "rolldown";
import { mkdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
const root = new URL("../../", import.meta.url);
const output = new URL("backend/src/agentfit/data/three_runtime.js", root);
await mkdir(new URL(".", output), { recursive: true });
const license = await readFile(new URL("../node_modules/three/LICENSE", import.meta.url), "utf8");
await build({
  input: fileURLToPath(new URL("../node_modules/three/build/three.module.js", import.meta.url)),
  output: { file: fileURLToPath(output), format: "iife", name: "THREE", minify: true,
    banner: "/*! Three.js — " + license.replaceAll("*/", "* /") + " */" },
});
