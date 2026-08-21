# TokenFold

Transparent token-compression middleware: you work in natural English,
the AI receives the cheapest reliable representation, and replies are
expanded back locally — token-free.

**Start here → [USER-GUIDE.md](USER-GUIDE.md)** (what it's for, how to use
it, and how the folding tech saves 75–91% of your tokens).

```
tokenfold/
├── USER-GUIDE.md    how to use it, use cases, how the savings work
├── windows/         Windows install + start/stop/autostart scripts
├── linux/           Linux install + start/stop scripts + systemd unit
├── core/            the cross-platform engine, proxy, tests, benchmarks
│   └── README.md    technical/architecture documentation
└── claude-plugin/   Claude Code plugin (skill + optional hook)
```

Quick start — Windows:
```
cd windows && powershell -ExecutionPolicy Bypass -File install.ps1 && .\start.ps1
```
Quick start — Linux:
```
cd linux && ./install.sh && ./start.sh
```
Then point any OpenAI-compatible client at `http://localhost:9339/v1`
and open the dashboard at `http://localhost:9339/tokenfold/dashboard`.
