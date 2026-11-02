import { Component, createSignal, For, Show, createMemo } from 'solid-js';
import { store, fmtMs } from '../state/store';
import { realignLine, purgeWork } from '../api';
import { Line, Token } from '../types';

interface FlaggedLineItem {
  idx: number;
  line: Line;
  score: number;
  issues: any[];
  diagFlags: any[];
  isUniform: boolean;
  hasDrift: boolean;
  hasComments: boolean;
  hasFlags: boolean;
}

export const DiagnosticsWindow: Component<{
  isDetached?: boolean;
  onClose?: () => void;
  onSeekTo?: (ms: number) => void;
  onPlayBounds?: (startMs: number, endMs?: number) => void;
}> = (props) => {
  const [filterMode, setFilterMode] = createSignal<'all' | 'flagged' | 'drift' | 'comments' | 'uniform'>('all');
  const [sortMode, setSortMode] = createSignal<'timeline' | 'confidence'>('timeline');
  const [realigningLineIdx, setRealigningLineIdx] = createSignal<number | null>(null);
  const [expandedIndices, setExpandedIndices] = createSignal<Set<number>>(new Set([store.activeLineIdx()]));

  const lines = () => store.lines();
  const lintReport = () => store.projectData()?.lint;
  const metadata = () => store.projectData()?.metadata;

  const totalLines = () => lines().length;

  const flaggedLines = createMemo<FlaggedLineItem[]>(() => {
    const lint = lintReport();
    const lns = lines();
    return lns.map((ln, idx) => {
      const lintLn = lint?.lines?.[idx];
      const issues = lintLn?.issues || [];
      const diagFlags = ln.diag?.flags || [];
      const score = lintLn?.score ?? (ln.diag?.mean_ctc_score ? Math.round(ln.diag.mean_ctc_score * 100) : 100);
      const isUniform = ln.diag?.alignment_method === 'uniform_fallback';
      const hasDrift = Math.abs(ln.diag?.shift_from_lrc_start_ms || 0) > 1000 || Math.abs(ln.diag?.shift_from_lrc_end_ms || 0) > 1000;
      const hasComments = ln.tokens.some((t) => t.comment && t.comment.trim());
      const hasFlags = issues.length > 0 || diagFlags.length > 0 || score < 80;

      return {
        idx,
        line: ln,
        score,
        issues,
        diagFlags,
        isUniform,
        hasDrift,
        hasComments,
        hasFlags,
      };
    });
  });

  const filteredLines = createMemo(() => {
    let list = flaggedLines();
    const mode = filterMode();
    if (mode === 'flagged') list = list.filter((i) => i.hasFlags);
    else if (mode === 'drift') list = list.filter((i) => i.hasDrift);
    else if (mode === 'comments') list = list.filter((i) => i.hasComments);
    else if (mode === 'uniform') list = list.filter((i) => i.isUniform);

    if (sortMode() === 'confidence') {
      return [...list].sort((a, b) => a.score - b.score);
    }
    return list;
  });

  const toggleExpand = (idx: number) => {
    const next = new Set(expandedIndices());
    if (next.has(idx)) next.delete(idx);
    else next.add(idx);
    setExpandedIndices(next);
  };

  const handleSeek = (ms: number) => {
    if (props.onSeekTo) props.onSeekTo(ms);
    else store.setCurrentTimeMs(ms);
  };

  const handlePlayLine = (startMs: number, endMs: number) => {
    if (props.onPlayBounds) props.onPlayBounds(Math.max(0, startMs - 200), endMs + 300);
    else store.sendStageCommand({ type: 'cmd_seek_ms', ms: Math.max(0, startMs - 200) });
  };

  const handleRealign = async (lineIdx: number, ignoreEndCap = false) => {
    const vid = store.currentVid();
    if (!vid) return;
    setRealigningLineIdx(lineIdx);
    try {
      const res = await realignLine(vid, lineIdx, { ignore_lrc_end_cap: ignoreEndCap });
      if (res.ok) {
        const lns = [...store.lines()];
        lns[lineIdx] = res.line;
        store.setLines(lns);
        if (res.lint && store.projectData()) {
          store.setProjectData({ ...store.projectData()!, lint: res.lint });
        }
        store.addToast(`⚡ Re-aligned Line #${lineIdx + 1} (${res.line.diag?.alignment_method || 'mms_fa'})`);
      }
    } catch (e: any) {
      store.addToast(`Failed to realign: ${e.message}`);
    } finally {
      setRealigningLineIdx(null);
    }
  };

  const copyDebugSnippet = (lineInfo?: FlaggedLineItem) => {
    const vid = store.currentVid();
    const meta = metadata();
    const report = {
      project_id: vid,
      title: meta?.title || vid,
      asr_engine: meta?.asr_engine || 'auto',
      target_lang: meta?.target_lang || 'auto',
      overall_qa_score: lintReport()?.mean_confidence ?? 100,
      timestamp: new Date().toISOString(),
      lines: (lineInfo ? [lineInfo] : flaggedLines().filter((i) => i.hasFlags || i.hasComments)).map((item) => ({
        line_idx: item.idx,
        text: item.line.tokens.map((t) => t.text).join(' '),
        start: item.line.start,
        end: item.line.end,
        score: item.score,
        lrc_nominal_start: item.line.diag?.lrc_nominal_start,
        lrc_nominal_end: item.line.diag?.lrc_nominal_end,
        shift_from_lrc_start_ms: item.line.diag?.shift_from_lrc_start_ms,
        shift_from_lrc_end_ms: item.line.diag?.shift_from_lrc_end_ms,
        alignment_method: item.line.diag?.alignment_method,
        mean_ctc_score: item.line.diag?.mean_ctc_score,
        mean_rms_db: item.line.diag?.mean_rms_db,
        lead_in_breath_trimmed_ms: item.line.diag?.lead_in_breath_trimmed_ms,
        tail_sustain_extended_ms: item.line.diag?.tail_sustain_extended_ms,
        asr_heard: item.line.diag?.asr_heard_text,
        issues: item.issues,
        diag_flags: item.diagFlags,
        tokens: item.line.tokens.map((t) => ({
          text: t.text,
          start: t.start,
          end: t.end,
          ctc_score: t.diag?.ctc_score,
          rms_db: t.diag?.rms_db,
          comment: t.comment,
        })),
      })),
    };

    const text = '```json\n' + JSON.stringify(report, null, 2) + '\n```';
    navigator.clipboard.writeText(text).then(() => {
      store.addToast(`📋 Copied ${lineInfo ? 'Line #' + (lineInfo.idx + 1) : 'Debug Report'} context to clipboard!`);
    }).catch(() => {
      store.addToast('Clipboard permission denied');
    });
  };

  const handlePurge = async () => {
    if (!window.confirm('Purge stale and legacy ASR/LRC files in work/ for clean v2 artifacts? Audio stems will be kept.')) return;
    try {
      const res = await purgeWork(store.currentVid(), true);
      store.addToast(`🗑️ Purged ${res.count} stale artifacts`);
    } catch (e: any) {
      store.addToast(`Purge failed: ${e.message}`);
    }
  };

  return (
    <div class={`diag-inspector-root ${props.isDetached ? 'detached-mode' : 'modal-mode'}`}>
      {/* Top Banner (Tier 1: Global & Language Sanity) */}
      <div class="diag-header-bar">
        <div class="diag-title-area">
          <span class="diag-badge-icon">🔬</span>
          <div>
            <div class="diag-title">Acoustic Alignment Diagnostics Inspector</div>
            <div class="diag-sub">
              Project: <strong>{metadata()?.title || store.currentVid()}</strong> ({store.currentVid()})
            </div>
          </div>
        </div>

        <div class="diag-tier1-stats">
          <div class="stat-box">
            <span class="stat-label">ASR Engine</span>
            <span class="stat-val font-mono">{metadata()?.asr_engine || 'auto'}</span>
          </div>
          <div class="stat-box">
            <span class="stat-label">Target Lang</span>
            <span class="stat-val">{metadata()?.target_lang || 'auto'}</span>
          </div>
          <div class="stat-box">
            <span class="stat-label">QA Quality</span>
            <span class={`stat-val font-mono ${lintReport()?.mean_confidence && lintReport()!.mean_confidence >= 80 ? 'text-green' : 'text-amber'}`}>
              {Math.round(lintReport()?.mean_confidence ?? 100)}%
            </span>
          </div>
          <div class="stat-box">
            <span class="stat-label">Flagged Lines</span>
            <span class="stat-val text-red">
              {flaggedLines().filter((i) => i.hasFlags).length} / {totalLines()}
            </span>
          </div>
        </div>

        <div class="diag-header-actions">
          <button class="btn btn-sm" onClick={() => copyDebugSnippet()} title="Copy all flagged lines debug context for AI">
            📋 Copy Debug Bundle
          </button>
          <button class="btn btn-sm btn-subtle" onClick={handlePurge} title="Clean legacy caches from work directory">
            🗑️ Clean Workspace
          </button>
          <Show when={!props.isDetached}>
            <button class="btn btn-sm" onClick={() => store.openDiagnosticsPopupWindow()} title="Open in dedicated companion window">
              ↗ Detach Window
            </button>
            <button class="btn btn-sm btn-icon" onClick={props.onClose} title="Close diagnostics drawer">
              ✕
            </button>
          </Show>
        </div>
      </div>

      {/* Filter and Sort Toolbar */}
      <div class="diag-filter-bar">
        <div class="filter-pills">
          <button
            class={`filter-pill ${filterMode() === 'all' ? 'active' : ''}`}
            onClick={() => setFilterMode('all')}
          >
            All ({totalLines()})
          </button>
          <button
            class={`filter-pill ${filterMode() === 'flagged' ? 'active' : ''}`}
            onClick={() => setFilterMode('flagged')}
          >
            🔴 Flagged Issues ({flaggedLines().filter((i) => i.hasFlags).length})
          </button>
          <button
            class={`filter-pill ${filterMode() === 'drift' ? 'active' : ''}`}
            onClick={() => setFilterMode('drift')}
          >
            🟡 Large LRC Drift ({flaggedLines().filter((i) => i.hasDrift).length})
          </button>
          <button
            class={`filter-pill ${filterMode() === 'comments' ? 'active' : ''}`}
            onClick={() => setFilterMode('comments')}
          >
            💬 Comments ({flaggedLines().filter((i) => i.hasComments).length})
          </button>
          <button
            class={`filter-pill ${filterMode() === 'uniform' ? 'active' : ''}`}
            onClick={() => setFilterMode('uniform')}
          >
            ⚠️ Uniform Fallbacks ({flaggedLines().filter((i) => i.isUniform).length})
          </button>
        </div>

        <div class="sort-controls">
          <span class="sort-label">Sort:</span>
          <button
            class={`sort-btn ${sortMode() === 'timeline' ? 'active' : ''}`}
            onClick={() => setSortMode('timeline')}
          >
            Timeline Order
          </button>
          <button
            class={`sort-btn ${sortMode() === 'confidence' ? 'active' : ''}`}
            onClick={() => setSortMode('confidence')}
          >
            Worst Confidence First
          </button>
        </div>
      </div>

      {/* Line-by-Line Multi-Track Comparison Cards */}
      <div class="diag-lines-scroll">
        <For each={filteredLines()}>
          {(item) => {
            const isExpanded = () => expandedIndices().has(item.idx);
            const line = item.line;
            const diag = line.diag;

            // Mini-timeline time boundaries spanning line.start - 800ms to line.end + 1200ms
            const nomS = diag?.lrc_nominal_start ?? line.start;
            const nomE = diag?.lrc_nominal_end ?? line.end;
            const minTime = Math.min(line.start, nomS) - 600;
            const maxTime = Math.max(line.end, nomE) + 800;
            const spanDuration = Math.max(1, maxTime - minTime);

            const toPct = (ms: number) => {
              return Math.max(0, Math.min(100, ((ms - minTime) / spanDuration) * 100));
            };

            const methodBadge = () => {
              const m = diag?.alignment_method || 'asr_anchored';
              if (m === 'mms_fa_ctc') return { label: '🟢 Meta MMS-FA CTC', class: 'badge-mms' };
              if (m === 'asr_anchored') return { label: '🔵 ASR Matched', class: 'badge-asr' };
              if (m === 'island_weighted') return { label: '🟡 Vocal Energy Islands', class: 'badge-island' };
              return { label: '🔴 Uniform Fallback', class: 'badge-uniform' };
            };

            return (
              <div class={`diag-line-card ${item.hasFlags ? 'has-issues' : ''} ${store.activeLineIdx() === item.idx ? 'is-active-line' : ''}`}>
                {/* Line Card Header */}
                <div class="diag-card-header" onClick={() => toggleExpand(item.idx)}>
                  <div class="line-meta-left">
                    <button class="expand-btn">{isExpanded() ? '▼' : '▶'}</button>
                    <span class="line-num">#{item.idx + 1}</span>
                    <span class="line-time font-mono">
                      {fmtMs(line.start)} – {fmtMs(line.end)} ({line.end - line.start}ms)
                    </span>
                    <span class={`method-badge ${methodBadge().class}`}>{methodBadge().label}</span>
                    <span class={`score-pill ${item.score >= 80 ? 'score-green' : item.score >= 60 ? 'score-yellow' : 'score-red'}`}>
                      {item.score}%
                    </span>
                  </div>

                  <div class="line-meta-right">
                    <Show when={diag?.shift_from_lrc_start_ms !== undefined}>
                      <span class={`lrc-shift-pill ${Math.abs(diag!.shift_from_lrc_start_ms || 0) > 1000 ? 'shift-warn' : ''}`}>
                        LRC Start: {diag!.shift_from_lrc_start_ms! >= 0 ? '+' : ''}{diag!.shift_from_lrc_start_ms}ms
                      </span>
                    </Show>

                    <div class="card-btn-group" onClick={(e) => e.stopPropagation()}>
                      <button
                        class="btn btn-xs"
                        onClick={() => handlePlayLine(line.start, line.end)}
                        title="Play line audio"
                      >
                        ▶ Play
                      </button>
                      <button
                        class="btn btn-xs"
                        onClick={() => {
                          store.setActiveLineIdx(item.idx);
                          handleSeek(line.start);
                        }}
                        title="Jump editor playhead to line"
                      >
                        🎯 Select
                      </button>
                      <button
                        class="btn btn-xs"
                        onClick={() => copyDebugSnippet(item)}
                        title="Copy structured debug bundle for this line"
                      >
                        📋 Debug
                      </button>
                      <div class="realign-dropdown">
                        <button
                          class="btn btn-xs btn-lead"
                          disabled={realigningLineIdx() === item.idx}
                          onClick={() => handleRealign(item.idx, false)}
                          title="Re-run MMS-FA forced alignment on this line"
                        >
                          {realigningLineIdx() === item.idx ? '⏳ Realigning...' : '⚡ Realign MMS-FA'}
                        </button>
                        <button
                          class="btn btn-xs btn-subtle"
                          disabled={realigningLineIdx() === item.idx}
                          onClick={() => handleRealign(item.idx, true)}
                          title="Re-align and extend past premature LRC end cap (+2.5s)"
                        >
                          +2.5s Sustain
                        </button>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Line Text Preview */}
                <div class="diag-line-text" onClick={() => toggleExpand(item.idx)}>
                  <span class="text-hanzi">
                    {line.tokens.map((t) => t.text + (t.space ? ' ' : '')).join('')}
                  </span>
                  <Show when={diag?.asr_heard_text}>
                    <span class="asr-preview" title="What the unconstrained ASR engine heard">
                      ASR heard: <em>"{diag!.asr_heard_text}"</em>
                    </span>
                  </Show>
                </div>

                {/* Issues and Flags Banner */}
                <Show when={item.issues.length > 0 || item.diagFlags.length > 0}>
                  <div class="diag-issues-list">
                    <For each={item.issues}>
                      {(iss) => (
                        <div class="diag-issue-item">
                          <span class="issue-code">{iss.rule}</span>
                          <span class="issue-detail">{iss.detail}</span>
                        </div>
                      )}
                    </For>
                    <For each={item.diagFlags}>
                      {(flg) => (
                        <div class={`diag-flag-item severity-${flg.severity}`}>
                          <span class="flag-code">{flg.code}</span>
                          <span class="flag-detail">{flg.detail}</span>
                        </div>
                      )}
                    </For>
                  </div>
                </Show>

                {/* Accordion Content: 4-Lane Mini-Timeline */}
                <Show when={isExpanded()}>
                  <div class="diag-timeline-accordion">
                    <div class="timeline-lane-labels">
                      <div class="lane-label">Vocal Energy / Islands</div>
                      <div class="lane-label">Nominal LRC Window</div>
                      <div class="lane-label">ASR Raw Tokens</div>
                      <div class="lane-label">Aligned Tokens (CTC Conf)</div>
                    </div>

                    <div class="timeline-lanes-container">
                      {/* Lane 1: Vocal Energy / Islands */}
                      <div class="timeline-lane lane-energy">
                        <Show when={diag?.voiced_islands && diag.voiced_islands.length > 0}>
                          <For each={diag!.voiced_islands}>
                            {(isl) => {
                              const left = toPct(isl[0]);
                              const width = Math.max(1, toPct(isl[1]) - left);
                              return (
                                <div
                                  class="voiced-island-pill"
                                  style={{ left: `${left}%`, width: `${width}%` }}
                                  title={`Voiced Energy Island: [${isl[0]}..${isl[1]}ms]`}
                                >
                                  Voiced ({isl[1] - isl[0]}ms)
                                </div>
                              );
                            }}
                          </For>
                        </Show>
                        <Show when={diag?.lead_in_breath_trimmed_ms && diag.lead_in_breath_trimmed_ms > 0}>
                          <div
                            class="breath-intake-pill"
                            style={{
                              left: `${toPct(nomS)}%`,
                              width: `${Math.max(1, toPct(nomS + diag!.lead_in_breath_trimmed_ms!) - toPct(nomS))}%`,
                            }}
                            title={`Breath Intake Trimmed: ${diag!.lead_in_breath_trimmed_ms}ms`}
                          >
                            💨 Breath Inhale
                          </div>
                        </Show>
                      </div>

                      {/* Lane 2: Nominal LRC Window */}
                      <div class="timeline-lane lane-lrc">
                        <div
                          class="lrc-nominal-bar"
                          style={{
                            left: `${toPct(nomS)}%`,
                            width: `${Math.max(2, toPct(nomE) - toPct(nomS))}%`,
                          }}
                          title={`Nominal LRC Prior: [${nomS}..${nomE}ms]`}
                        >
                          Nominal LRC: {fmtMs(nomS)} – {fmtMs(nomE)}
                        </div>
                      </div>

                      {/* Lane 3: ASR Raw Tokens */}
                      <div class="timeline-lane lane-asr">
                        <Show when={diag?.asr_raw_tokens && diag.asr_raw_tokens.length > 0} fallback={<span class="muted-lane-text">(No direct token matches)</span>}>
                          <For each={diag!.asr_raw_tokens}>
                            {(tok) => {
                              const left = toPct(tok.start);
                              const width = Math.max(1.5, toPct(tok.end) - left);
                              return (
                                <div
                                  class="asr-token-chip"
                                  style={{ left: `${left}%`, width: `${width}%` }}
                                  title={`ASR Word '${tok.text}': [${tok.start}..${tok.end}ms]`}
                                >
                                  {tok.text}
                                </div>
                              );
                            }}
                          </For>
                        </Show>
                      </div>

                      {/* Lane 4: Final Aligned Tokens (CTC Colored) */}
                      <div class="timeline-lane lane-tokens">
                        <For each={line.tokens.filter((t) => !t.is_deleted)}>
                          {(tok) => {
                            const left = toPct(tok.start);
                            const width = Math.max(1.8, toPct(tok.end) - left);
                            const ctc = tok.diag?.ctc_score ?? 0.8;
                            const colorClass = ctc >= 0.7 ? 'ctc-high' : ctc >= 0.4 ? 'ctc-med' : 'ctc-low';

                            return (
                              <div
                                class={`token-aligned-chip ${colorClass}`}
                                style={{ left: `${left}%`, width: `${width}%` }}
                                onClick={() => handlePlayLine(tok.start, tok.end)}
                                title={`'${tok.text}' [${tok.start}..${tok.end}ms] (${tok.end - tok.start}ms)\nCTC Score: ${Math.round(ctc * 100)}%\nRMS: ${tok.diag?.rms_db ?? 'N/A'} dBFS\nOnset: ${tok.diag?.onset_strength ?? 'N/A'}\nSustain Ext: +${tok.diag?.sustain_extended_ms ?? 0}ms`}
                              >
                                <span class="chip-txt">{tok.text}</span>
                                <span class="chip-ctc">{Math.round(ctc * 100)}%</span>
                              </div>
                            );
                          }}
                        </For>
                      </div>
                    </div>

                    {/* Acoustic Telemetry Readout */}
                    <div class="diag-telemetry-grid">
                      <div class="tele-item">
                        <span class="tele-key">Mean CTC Score:</span>
                        <span class="tele-val font-mono">{(diag?.mean_ctc_score ?? 0.8).toFixed(2)}</span>
                      </div>
                      <div class="tele-item">
                        <span class="tele-key">Acoustic RMS Energy:</span>
                        <span class="tele-val font-mono">{(diag?.mean_rms_db ?? -20).toFixed(1)} dBFS</span>
                      </div>
                      <div class="tele-item">
                        <span class="tele-key">Phonetic Match:</span>
                        <span class="tele-val font-mono">{diag?.phonetic_match_score ?? 100}%</span>
                      </div>
                      <div class="tele-item">
                        <span class="tele-key">Lead Breath Trimmed:</span>
                        <span class="tele-val font-mono">+{diag?.lead_in_breath_trimmed_ms ?? 0}ms</span>
                      </div>
                      <div class="tele-item">
                        <span class="tele-key">Tail Sustain Extended:</span>
                        <span class="tele-val font-mono">+{diag?.tail_sustain_extended_ms ?? 0}ms</span>
                      </div>
                    </div>
                  </div>
                </Show>
              </div>
            );
          }}
        </For>
      </div>
    </div>
  );
};
