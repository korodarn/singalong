import { Component, For } from 'solid-js';
import { store } from '../state/store';

export const HeaderBar: Component<{ onSelectAndSeekLine?: (idx: number) => void }> = (props) => {
  const qaScore = () => store.projectData()?.lint?.mean_confidence ?? 100;
  const totalIssues = () => store.projectData()?.lint?.total_issues ?? 0;

  const scoreClass = () => {
    const s = qaScore();
    if (s >= 85) return 'score-green';
    if (s >= 70) return 'score-yellow';
    return 'score-red';
  };

  const jumpIssue = (dir: -1 | 1) => {
    const lint = store.projectData()?.lint;
    if (!lint || !lint.lines.length) return;
    const total = lint.lines.length;
    let nextIdx = store.activeLineIdx();

    for (let step = 1; step <= total; step++) {
      nextIdx = (nextIdx + dir + total) % total;
      if (lint.lines[nextIdx] && lint.lines[nextIdx].issues && lint.lines[nextIdx].issues.length > 0) {
        store.setActiveLineIdx(nextIdx);
        if (props.onSelectAndSeekLine) props.onSelectAndSeekLine(nextIdx);
        return;
      }
    }
  };

  const openIngestWithInput = () => {
    store.setShowIngestModal(true);
  };

  const openRealignCurrent = () => {
    if (store.currentVid()) {
      store.setHeaderInputVid(store.currentVid());
    }
    store.setShowIngestModal(true);
  };

  return (
    <header class="header">
      <div class="header-left">
        <div class="brand">
          <span>🎤 SINGALONG STUDIO</span>
          <span class="brand-badge">Workbench</span>
        </div>

        <div class="ingest-bar" title="Enter a YouTube Video ID (like XEGWxPmWTYs) or URL to run Pipeline 1 and create its JSON">
          <input
            type="text"
            class="yt-input"
            placeholder="YouTube ID / URL (e.g. XEGWxPmWTYs)"
            value={store.headerInputVid()}
            onInput={(e) => store.setHeaderInputVid(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') openIngestWithInput();
            }}
          />
          <button class="btn btn-lead" style={{ padding: '4px 10px' }} onClick={openIngestWithInput}>
            🚀 Import / Create JSON
          </button>
        </div>

        <select
          class="project-select"
          value={store.currentVid()}
          onChange={(e) => store.selectProject(e.currentTarget.value)}
          title="Switch between generated projects in work/"
        >
          {store.projects().length === 0 ? (
            <option value="">(No projects yet — Import a YouTube ID)</option>
          ) : null}
          <For each={store.projects()}>
            {(p) => (
              <option value={p.id}>
                {p.id} ({p.num_lines} lines){p.has_vocals ? ' 🎤' : ''}
              </option>
            )}
          </For>
        </select>
      </div>

      <div class="header-center">
        <div class="btn-group">
          <button
            class={`btn-toggle ${store.stemMode() === 'vocals' ? 'active' : ''}`}
            onClick={() => store.setStemMode('vocals')}
            title="Listen to isolated vocal stem (V)"
          >
            🎤 Vocals (V)
          </button>
          <button
            class={`btn-toggle ${store.stemMode() === 'mix' ? 'active' : ''}`}
            onClick={() => store.setStemMode('mix')}
            title="Listen to full original mix (V)"
          >
            🎵 Full Mix
          </button>
        </div>

        <div class="qa-nav">
          <span class={`score-chip ${scoreClass()}`}>QA: {Math.round(qaScore())}%</span>
          <button class="btn" style={{ padding: '3px 7px', 'font-size': '11px' }} onClick={() => jumpIssue(-1)} title="Previous Flagged Issue (P)">
            ◀
          </button>
          <span style={{ 'font-size': '11px', color: 'var(--text-muted)', 'font-family': 'var(--font-mono)' }}>
            {totalIssues()} {totalIssues() === 1 ? 'issue' : 'issues'}
          </span>
          <button class="btn" style={{ padding: '3px 7px', 'font-size': '11px' }} onClick={() => jumpIssue(1)} title="Next Flagged Issue (N)">
            ▶
          </button>
        </div>
      </div>

      <div class="header-right">
        <button class="btn" onClick={openRealignCurrent} title="Re-run singalong.py alignment or change NetEase lyrics query">
          🔄 Re-Align
        </button>

        <button class="btn" onClick={() => store.undo()} title="Undo last edit (Ctrl+Z)">
          ↩ Undo
        </button>

        <button class="btn btn-primary" onClick={() => store.saveCurrentProject()} disabled={store.isSaving()}>
          {store.isSaving() ? '💾 Saving...' : '💾 Save (Ctrl+S)'}
        </button>

        <button class="btn btn-success" onClick={() => store.setShowRenderModal(true)}>
          🎬 Render Video
        </button>
      </div>
    </header>
  );
};
