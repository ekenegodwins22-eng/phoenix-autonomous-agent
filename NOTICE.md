# Third-party notices

The Phoenix Console (`app/static/console.html`) adapts its layout and visual patterns from the browser-use cloud dashboard: a minimal top bar, a centered prompt card with paperclip, settings, model and effort selectors, lightning and a circular submit button, a task title bar with Share and Files, a chat thread with a "Worked for Xs" activity line, and a persistent bottom composer. No browser-use source code is copied into this repository; only layout and patterns are adapted. The model and proxy configuration is Phoenix's own, served by this server.

browser-use
https://github.com/browser-use/browser-use
Copyright (c) browser-use contributors.

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


## Vendored browser-use engine

`vendor/browser_use_upstream/` contains the complete browser-use Python package source from the upstream repository, tag 0.13.13 (commit 129853546ff9cc169977d38b6038f9c4a4e734db), copied unmodified. Its MIT LICENSE is at `vendor/browser_use_upstream/LICENSE`, and the upstream pyproject is kept beside it. Copyright (c) 2024 Gregor Zunic and browser-use contributors. Source: https://github.com/browser-use/browser-use

The only Phoenix change to the engine's behavior is the LLM layer: `app/browser_use_adapter.py` supplies `PhoenixRouterChatModel`, which routes calls through Phoenix's own provider stack (app/providers.py) and Phoenix's proxy settings. Phoenix's AGENT.md is passed as the agent's system extension. No browser-use cloud service or API key is used.
