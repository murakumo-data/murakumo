# Murakumo Canvas

The visual modelling canvas: an ERD editor built with React, TypeScript, Vite and
[React Flow](https://reactflow.dev). It reads and writes a project's model through the API that
`murakumo canvas` serves; see [docs/canvas.md](../../docs/canvas.md).

```bash
npm install
npm run build   # dist/, which `murakumo canvas` serves
npm run lint
```

To work on it, run the API and Vite side by side; Vite forwards `/api` to port 8765:

```bash
cd ../../packages/python && uv run murakumo canvas ../../examples/commerce --no-open
npm run dev     # http://localhost:5173
```

| File | What it does |
| --- | --- |
| `src/api.ts` | API client and the IR types the canvas uses |
| `src/App.tsx` | Loading, polling for changes on disk, the toolbar, drag-to-relate |
| `src/EntityNode.tsx` | An entity box, with a handle on each side of every column |
| `src/Inspector.tsx` | The side panel that edits an entity or relationship |
| `src/layout.ts` | Where entities without a saved position go |
