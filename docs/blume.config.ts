import { defineConfig } from "blume";

export default defineConfig({
  title: "pdf-textlayer",
  description:
    "Add an invisible, searchable text layer to a PDF from OCR JSON. One Rust core, with a CLI, a Python package, and a Node.js package.",
  logo: "/logo.svg",
  content: {
    root: "content",
  },
  theme: {
    accent: "teal",
  },
  github: {
    owner: "patelvivekdev",
    repo: "pdf-textlayer",
    dir: "docs",
  },
  navigation: {
    actions: [{ label: "Changelog", href: "https://github.com/patelvivekdev/pdf-textlayer/releases" }],
  },
  footer: {
    copyright: "MIT License © Vivek Patel",
    links: [
      { label: "PyPI", href: "https://pypi.org/project/pdf-textlayer/" },
      { label: "npm", href: "https://www.npmjs.com/package/pdf-textlayer" },
      { label: "crates.io", href: "https://crates.io/crates/pdf-textlayer" },
    ],
  },
  agents: {
    llmsTxt: true,
  },
});
