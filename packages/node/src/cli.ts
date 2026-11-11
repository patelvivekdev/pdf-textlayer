#!/usr/bin/env node
// Entry point of the `pdf-textlayer` command. The command itself is Rust.

import { native } from "./native.js";

process.exitCode = native.runCli(["pdf-textlayer", ...process.argv.slice(2)]);
