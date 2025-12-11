# Santo Development TODO

## 1. Data File Storage Strategy

TODO: Determine how/where to store uploaded data files during processing.

**Current state**: Files stored in `src/stage_1_upload/uploads/` with no cleanup policy.

**Questions to resolve**:
- Where should files live? (local disk, temp dir, cloud storage)
- How long should files persist? (session-scoped, TTL-based, explicit delete)
- Single-user vs multi-user considerations
- Docker volume mounting implications

---

## 2. Vite Frontend Conversion

Migrate vanilla JS/CSS to Vite for dev experience and production optimization.

### Current State

```
src/
├── stage_1_upload/static/
│   ├── stage_1_upload.js
│   └── stage_1_upload.css
├── stage_2_review_columns/static/
│   └── stage_2_mappings.js
├── stage_3_harmonize/static/
│   ├── stage_3_harmonize.js
│   ├── stage_3_harmonize.css
│   └── metrics/
│       ├── dashboard.js
│       ├── manifest_adapter.js
│       ├── change_split_viz.js
│       ├── change_types_viz.js
│       ├── confidence_buckets_viz.js
│       └── total_items_viz.js
├── stage_4_review_results/static/
│   ├── stage_4_review.js
│   └── stage_4_review.css
└── stage_5_review_summary/static/
    ├── stage_5_review.js
    └── stage_5_review.css
```

### Target Structure

```
santo/
├── frontend/
│   ├── package.json
│   ├── vite.config.js
│   ├── src/
│   │   ├── stages/
│   │   │   ├── stage-1/
│   │   │   │   ├── index.js
│   │   │   │   └── styles.css
│   │   │   ├── stage-2/
│   │   │   ├── stage-3/
│   │   │   │   ├── index.js
│   │   │   │   ├── styles.css
│   │   │   │   └── metrics/
│   │   │   ├── stage-4/
│   │   │   └── stage-5/
│   │   └── shared/
│   │       └── common.css (if extracted)
│   └── dist/              # Build output
├── backend/
└── src/                   # Keep templates here, update asset paths
```

### Implementation Steps

#### Phase 1: Setup

- [ ] Create `frontend/` directory at repo root
- [ ] Initialize with `npm init -y`
- [ ] Install Vite: `npm install -D vite`
- [ ] Create `vite.config.js` with multi-entry build configuration

#### Phase 2: Migration

- [ ] Move JS files to `frontend/src/stages/`
- [ ] Move CSS files alongside their JS counterparts
- [ ] Update any relative imports between JS files (stage 3 metrics)
- [ ] Add entry points in vite.config.js for each stage

#### Phase 3: Backend Integration

- [ ] Update `backend/app/main.py` to serve `frontend/dist/` as static
- [ ] Update Jinja templates to reference new asset paths
- [ ] Add build step to Dockerfile: `npm run build`

#### Phase 4: Development Workflow

- [ ] Add npm scripts: `dev`, `build`, `preview`
- [ ] Document: run `npm run dev` for HMR during frontend work
- [ ] Production: `npm run build` produces optimized bundles

### vite.config.js Sketch

```javascript
import { defineConfig } from 'vite';
import { resolve } from 'path';

export default defineConfig({
  root: 'src',
  build: {
    outDir: '../dist',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        stage1: resolve(__dirname, 'src/stages/stage-1/index.js'),
        stage2: resolve(__dirname, 'src/stages/stage-2/index.js'),
        stage3: resolve(__dirname, 'src/stages/stage-3/index.js'),
        stage4: resolve(__dirname, 'src/stages/stage-4/index.js'),
        stage5: resolve(__dirname, 'src/stages/stage-5/index.js'),
      },
      output: {
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash].[ext]',
      },
    },
  },
});
```

### Considerations

- **Jinja + Vite**: Use `vite-plugin-jinja` or generate a manifest.json that backend reads for hashed filenames
- **Dev proxy**: Vite dev server can proxy API calls to FastAPI backend
- **CSS modules**: Optional - can adopt gradually
- **TypeScript**: Optional future enhancement

### Files to Create

```
frontend/package.json
frontend/vite.config.js
frontend/src/stages/stage-1/index.js
frontend/src/stages/stage-1/styles.css
frontend/src/stages/stage-2/index.js
frontend/src/stages/stage-3/index.js
frontend/src/stages/stage-3/styles.css
frontend/src/stages/stage-3/metrics/*.js
frontend/src/stages/stage-4/index.js
frontend/src/stages/stage-4/styles.css
frontend/src/stages/stage-5/index.js
frontend/src/stages/stage-5/styles.css
```

### Files to Modify

```
backend/app/main.py           # Update static file serving
src/stage_*/templates/*.html  # Update asset <script>/<link> paths
Dockerfile                    # Add Node.js and npm build step
```

---

## Priority

1. **File cleanup** - Quick win, prevents disk issues
2. **Vite conversion** - Larger effort, better dev experience
