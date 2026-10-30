import { Component, For, Show } from 'solid-js';
import { store } from '../state/store';
import { updateProjectMeta } from '../api';

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

  const currentTitle = () => store.projectData()?.metadata?.title || store.currentVid() || 'No Project Loaded';
  const currentArtist = () => store.projectData()?.metadata?.artist || '';
  const durationSec = () => store.projectData()?.metadata?.duration || 0;

  const formattedDuration = () => {
    const d = durationSec();
    if (!d) return null;
    const m = Math.floor(d / 60);
    const s = Math.floor(d % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };

  const totalTokens = () => {
    let count = 0;
    for (const ln of store.lines()) {
      count += ln.tokens.filter((t) => !t.is_deleted).length;
    }
    return count;
  };

  const scriptDistribution = () => {
    let lat = 0;
    let han = 0;
    let ko = 0;
    let ja = 0;
    for (const ln of store.lines()) {
      for (const t of ln.tokens) {
        if (t.is_deleted) continue;
        for (const ch of t.text) {
          const code = ch.charCodeAt(0);
          if ((code >= 65 && code <= 90) || (code >= 97 && code <= 122)) lat++;
          else if (code >= 0x4e00 && code <= 0x9fff) han++;
          else if (code >= 0xac00 && code <= 0xd7af) ko++;
          else if ((code >= 0x3040 && code <= 0x309f) || (code >= 0x30a0 && code <= 0x30ff)) ja++;
        }
      }
    }
    const tot = lat + han + ko + ja;
    if (tot === 0) return null;
    if (ko / tot > 0.25) return `Korean (${Math.round((ko / tot) * 100)}% Hangul)`;
    if (ja / tot > 0.1 || (han / tot > 0.3 && ja / tot > 0.05)) {
      return `Japanese (${Math.round(((ja + han) / tot) * 100)}% Kana/Kanji)`;
    }
    if (han / tot > 0.4) return `Mandarin (${Math.round((han / tot) * 100)}% Hanzi)`;
    if (lat / tot > 0.6) return `English (${Math.round((lat / tot) * 100)}% Latin)`;
    return `Mixed (${Math.round((lat / tot) * 100)}% Latin, ${Math.round((han / tot) * 100)}% CJK)`;
  };

  const handleTierChange = async (mode: '3' | '2' | '1') => {
    store.setTierMode(mode);
    const vid = store.currentVid();
    if (!vid) return;
    try {
      await updateProjectMeta(vid, { tier_mode: mode });
    } catch (e) {
      // ignore
    }
  };

  const handleEditTitle = async () => {
    const vid = store.currentVid();
    if (!vid) return;
    const currentT = currentTitle();
    const currentA = currentArtist();
    const newTitle = window.prompt('Enter Song Title:', currentT);
    if (newTitle === null) return;
    const newArtist = window.prompt('Enter Artist / Channel:', currentA);
    if (newArtist === null) return;

    try {
      const res = await updateProjectMeta(vid, { title: newTitle.trim(), artist: newArtist.trim() });
      if (res.ok) {
        if (store.projectData()) {
          store.setProjectData({
            ...store.projectData()!,
            metadata: res.metadata,
          });
        }
        await store.loadProjectList();
        store.addToast('✅ Updated song metadata');
      }
    } catch (e: any) {
      store.addToast(`Failed to update metadata: ${e.message}`);
    }
  };

  return (
    <header class="header">
      <div class="header-main-row">
        <div class="header-left">
          <div class="brand">
            <span>🎤 SINGALONG STUDIO</span>
            <span class="brand-badge">Workbench</span>
          </div>

          <div class="ingest-bar" title="Enter a YouTube Video ID or URL to import lyrics and create its JSON">
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
              {(p) => {
                const label = p.title && p.title !== p.id ? `${p.title} (${p.id})` : p.id;
                return (
                  <option value={p.id}>
                    {label} — {p.num_lines} lines{p.has_vocals ? ' 🎤' : ''}
                  </option>
                );
              }}
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
            <button
              class="btn"
              style={{ padding: '3px 7px', 'font-size': '11px' }}
              onClick={() => jumpIssue(-1)}
              title="Previous Flagged Issue (P)"
            >
              ◀
            </button>
            <span style={{ 'font-size': '11px', color: 'var(--text-muted)', 'font-family': 'var(--font-mono)' }}>
              {totalIssues()} {totalIssues() === 1 ? 'issue' : 'issues'}
            </span>
            <button
              class="btn"
              style={{ padding: '3px 7px', 'font-size': '11px' }}
              onClick={() => jumpIssue(1)}
              title="Next Flagged Issue (N)"
            >
              ▶
            </button>
          </div>
        </div>

        <div class="header-right">
          <button
            class="btn"
            onClick={() => store.setShowVideoBgModal(true)}
            title="Set or adjust background video or image overlay"
          >
            🎬 Background
          </button>

          <button
            class="btn"
            onClick={openRealignCurrent}
            title="Re-run singalong.py alignment or change lyrics query/drop-in"
          >
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
      </div>

      <Show when={store.currentVid()}>
        <div class="metadata-strip">
          <div class="meta-strip-left">
            <span class="meta-song-title" title="Song Title">🎵 {currentTitle()}</span>
            <Show when={currentArtist()}>
              <span class="meta-song-artist" title="Artist / Channel">👤 {currentArtist()}</span>
            </Show>
            <span class="meta-badge-vid" title="YouTube Video ID">🆔 {store.currentVid()}</span>
          </div>

          <div class="meta-strip-right">
            <span class="meta-stat">📊 {store.lines().length} lines</span>
            <span class="meta-stat">🔤 {totalTokens()} syllables</span>
            <Show when={formattedDuration()}>
              <span class="meta-stat">⏱️ {formattedDuration()}</span>
            </Show>
            <Show when={scriptDistribution()}>
              <span class="meta-stat-script">🌐 {scriptDistribution()}</span>
            </Show>
            <div class="btn-group" title="Select subtitle display tiers for Stage & Render">
              <button
                class={`btn-toggle ${store.tierMode() === '3' ? 'active' : ''}`}
                style={{ padding: '2px 7px', 'font-size': '11px' }}
                onClick={() => handleTierChange('3')}
              >
                3-Tier
              </button>
              <button
                class={`btn-toggle ${store.tierMode() === '2' ? 'active' : ''}`}
                style={{ padding: '2px 7px', 'font-size': '11px' }}
                onClick={() => handleTierChange('2')}
              >
                2-Tier
              </button>
              <button
                class={`btn-toggle ${store.tierMode() === '1' ? 'active' : ''}`}
                style={{ padding: '2px 7px', 'font-size': '11px' }}
                onClick={() => handleTierChange('1')}
              >
                1-Tier
              </button>
            </div>
            <button class="btn-meta-edit" onClick={handleEditTitle} title="Rename song title / artist">
              ✏️ Edit Title
            </button>
          </div>
        </div>
      </Show>
    </header>
  );
};
