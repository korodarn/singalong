import { Component, For, Show, createEffect, createSignal } from 'solid-js';
import { store, fmtMs } from '../state/store';

export const LyricTable: Component<{
  onSeekTo: (ms: number) => void;
  onPlayBounds: (startMs: number, endMs?: number) => void;
}> = (props) => {
  let tableContainerRef!: HTMLDivElement;
  let wheelDebounceTimer: any = null;

  const [dragInfo, setDragInfo] = createSignal<{
    lineIdx: number;
    tokenIndices: number[];
    deltaMs: number;
    previewStartMs: number;
    previewEndMs: number;
  } | null>(null);

  // Auto-scroll active row into view when activeLineIdx changes
  createEffect(() => {
    const idx = store.activeLineIdx();
    const row = document.getElementById(`line-row-${idx}`);
    if (row && tableContainerRef) {
      row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  });

  const lineIssues = (lineIdx: number) => {
    const lintLines = store.projectData()?.lint?.lines;
    if (!lintLines || !lintLines[lineIdx]) return [];
    return lintLines[lineIdx].issues || [];
  };

  const startTokenDrag = (
    e: MouseEvent,
    lineIdx: number,
    tokIdx: number,
    tok: any,
    isSelected: boolean
  ) => {
    if (e.button !== 0) return;

    const targetEl = e.target as HTMLElement;
    if (targetEl.closest('.tok-act-btn') || targetEl.closest('.tok-comment-dot')) {
      return;
    }

    if (e.altKey) {
      e.stopPropagation();
      store.toggleTokenDeleted(lineIdx, tokIdx);
      return;
    }
    if (e.shiftKey) {
      e.stopPropagation();
      if (store.activeLineIdx() !== lineIdx) store.setActiveLineIdx(lineIdx);
      store.shiftSelectToken(tokIdx);
      return;
    }
    if (e.ctrlKey || e.metaKey) {
      e.stopPropagation();
      if (store.activeLineIdx() !== lineIdx) store.setActiveLineIdx(lineIdx);
      store.toggleSelectToken(tokIdx);
      return;
    }

    e.preventDefault();
    e.stopPropagation();

    if (store.activeLineIdx() !== lineIdx) {
      store.setActiveLineIdx(lineIdx);
    }

    if (!isSelected) {
      store.selectSingleToken(tokIdx);
    }

    const sel = store.selectedTokenIndices();
    const targetIndices = sel.includes(tokIdx) ? [...sel] : [tokIdx];
    const curLine = store.lines()[lineIdx];
    if (!curLine) return;

    const targetTokens = targetIndices.map((i) => curLine.tokens[i]).filter(Boolean);
    const initialStartMs = Math.min(...targetTokens.map((t) => t.start));
    const initialEndMs = Math.max(...targetTokens.map((t) => t.end));

    const startX = e.clientX;
    let didDrag = false;
    let accumulatedDeltaMs = 0;

    const onMouseMove = (ev: MouseEvent) => {
      const distPx = ev.clientX - startX;
      if (!didDrag && Math.abs(distPx) > 3) {
        didDrag = true;
      }
      if (didDrag) {
        // Slow and fine: 3ms per pixel drag
        accumulatedDeltaMs = Math.round(distPx * 3);
        const curStart = Math.max(0, initialStartMs + accumulatedDeltaMs);
        const curEnd = Math.max(curStart + 30, initialEndMs + accumulatedDeltaMs);
        setDragInfo({
          lineIdx,
          tokenIndices: targetIndices,
          deltaMs: accumulatedDeltaMs,
          previewStartMs: curStart,
          previewEndMs: curEnd,
        });
        props.onSeekTo(curStart);
      }
    };

    const onMouseUp = () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);

      if (didDrag) {
        setDragInfo(null);
        if (accumulatedDeltaMs !== 0) {
          store.shiftTokens(lineIdx, targetIndices, accumulatedDeltaMs, true);
        }
        const finalStart = Math.max(0, initialStartMs + accumulatedDeltaMs);
        const finalEnd = Math.max(finalStart + 30, initialEndMs + accumulatedDeltaMs);
        props.onPlayBounds(finalStart, finalEnd + 80);
      } else {
        // Just clicked without dragging:
        // If there was a multi-selection, a clean click collapses it to this single token
        if (sel.length > 1) {
          store.selectSingleToken(tokIdx);
        }
        props.onPlayBounds(tok.start, tok.end + 80);
      }
    };

    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
  };

  const handleTokenWheel = (e: WheelEvent, lineIdx: number, tokIdx: number) => {
    e.preventDefault();
    e.stopPropagation();

    if (store.activeLineIdx() !== lineIdx) {
      store.setActiveLineIdx(lineIdx);
    }

    const sel = store.selectedTokenIndices();
    const targetIndices = sel.includes(tokIdx) ? sel : [tokIdx];

    // deltaY < 0 = scroll UP = extend duration (+25ms)
    // deltaY > 0 = scroll DOWN = reduce duration (-25ms)
    const deltaEndMs = e.deltaY < 0 ? 25 : -25;
    store.adjustTokenDuration(lineIdx, targetIndices, deltaEndMs, true);

    const curLine = store.lines()[lineIdx];
    if (curLine) {
      const toks = targetIndices.map((i) => curLine.tokens[i]).filter(Boolean);
      if (toks.length > 0) {
        const minS = Math.min(...toks.map((t) => t.start));
        const maxE = Math.max(...toks.map((t) => t.end));
        props.onSeekTo(maxE);

        clearTimeout(wheelDebounceTimer);
        wheelDebounceTimer = setTimeout(() => {
          props.onPlayBounds(minS, maxE + 80);
        }, 180);
      }
    }
  };

  return (
    <div ref={tableContainerRef} class="table-container">
      <Show when={dragInfo()}>
        <div class="drag-preview-banner">
          <span>
            ↔ Shifting {dragInfo()!.tokenIndices.length} syllable(s):{' '}
            <strong style={{ color: dragInfo()!.deltaMs >= 0 ? '#38ef7d' : '#ff9f43' }}>
              {dragInfo()!.deltaMs >= 0 ? '+' : ''}{dragInfo()!.deltaMs}ms
            </strong>{' '}
            (Preview: {fmtMs(dragInfo()!.previewStartMs)} → {fmtMs(dragInfo()!.previewEndMs)})
          </span>
          <span style={{ 'font-size': '11px', color: 'var(--text-muted)' }}>
            Release mouse to drop & listen
          </span>
        </div>
      </Show>

      <table class="lyrics-table">
        <thead>
          <tr>
            <th style={{ width: '48px' }}>#</th>
            <th style={{ width: '155px' }}>Time Range</th>
            <th style={{ width: '95px' }}>Track</th>
            <th>
              Syllables, Pinyin & US Phonetics{' '}
              <span style={{ 'font-weight': 'normal', color: 'var(--text-dim)', 'margin-left': '8px' }}>
                (Click: Select • Space: Play selection • Drag chip: Fine shift • Wheel on chip: Expand/trim tail • Double-Click: Edit Phonetics)
              </span>
            </th>
          </tr>
        </thead>
        <tbody>
          <For each={store.lines()}>
            {(line, lineIdx) => {
              const isActiveLine = () => store.activeLineIdx() === lineIdx();
              const isFirstLine = () => lineIdx() === 0;
              const isLastLine = () => lineIdx() >= store.lines().length - 1;
              const issues = () => lineIssues(lineIdx());
              const commentsInLine = () => (line.tokens || []).filter((t) => t.comment && t.comment.trim());

              return (
                <tr
                  id={`line-row-${lineIdx()}`}
                  class={`table-row ${isActiveLine() ? 'row-active' : ''}`}
                  onClick={(e) => {
                    const targetEl = e.target as HTMLElement;
                    if (!targetEl.closest('.tok-chip') && !targetEl.closest('button')) {
                      store.setActiveLineIdx(lineIdx());
                      store.clearTokenSelection();
                    }
                  }}
                >
                  <td class="col-idx">#{String(lineIdx() + 1).padStart(2, '0')}</td>
                  <td class="col-time">
                    <button
                      class="btn-time"
                      onClick={(e) => {
                        e.stopPropagation();
                        store.setActiveLineIdx(lineIdx());
                        props.onPlayBounds(line.start, line.end + 150);
                      }}
                      title="Click to play this line"
                    >
                      ▶ {fmtMs(line.start)} → {fmtMs(line.end)}
                    </button>
                    <div class="row-badges">
                      <For each={issues()}>
                        {(iss) => (
                          <span class="issue-pill" title={iss.detail}>
                            ⚠️ {iss.rule.replace('WARN_', '').replace('ERR_', '')}
                          </span>
                        )}
                      </For>
                      <Show when={commentsInLine().length > 0}>
                        <span
                          class="line-comment-pill"
                          title={commentsInLine()
                            .map((t) => `${t.text}: ${t.comment}`)
                            .join('\n')}
                        >
                          💬 {commentsInLine().length} note{commentsInLine().length > 1 ? 's' : ''}
                        </span>
                      </Show>
                    </div>
                  </td>
                  <td class="col-track">
                    <button
                      class={`badge-track ${line.is_backing ? 'badge-cyan' : 'badge-gold'}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        store.toggleLineBacking(lineIdx());
                      }}
                      title="Click to toggle Lead / Backing track (B)"
                    >
                      {line.is_backing ? '👥 Backing' : '🎤 Lead'}
                    </button>
                  </td>
                  <td class="col-tokens">
                    <div class="tokens-wrapper">
                      <For each={line.tokens}>
                        {(tok, tokIdx) => {
                          const isSelected = () =>
                            isActiveLine() && store.selectedTokenIndices().includes(tokIdx());
                          const isPlayingNow = () => {
                            const t = store.currentTimeMs();
                            return t >= tok.start && t <= tok.end;
                          };

                          return (
                            <div
                              class={`tok-chip ${tok.space ? 'tok-space' : ''} ${tok.is_deleted ? 'tok-deleted' : ''} ${
                                isSelected() ? 'tok-selected' : ''
                              } ${isPlayingNow() ? 'tok-playing' : ''}`}
                              onMouseDown={(e) => startTokenDrag(e, lineIdx(), tokIdx(), tok, isSelected())}
                              onWheel={(e) => handleTokenWheel(e, lineIdx(), tokIdx())}
                              onDblClick={(e) => {
                                e.stopPropagation();
                                store.setActiveLineIdx(lineIdx());
                                store.setEditingToken({ lineIdx: lineIdx(), tokenIdx: tokIdx(), token: tok });
                              }}
                              title={`[${tok.start}ms..${tok.end}ms (${tok.end - tok.start}ms)] Left-drag: Shift timing | Wheel: Expand/trim duration | Double-click: Edit Phonetics${
                                tok.comment ? ' | Note: ' + tok.comment : ''
                              }`}
                            >
                              {/* Hover mini-toolbar on each syllable chip */}
                              <div class="tok-chip-actions">
                                <button
                                  class="tok-act-btn"
                                  disabled={isFirstLine()}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    store.setActiveLineIdx(lineIdx());
                                    store.pushSyllableToPrevLine(lineIdx(), tokIdx());
                                  }}
                                  title="Push to previous line"
                                >
                                  ⬆
                                </button>
                                <Show when={tokIdx() > 0}>
                                  <button
                                    class="tok-act-btn"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      store.setActiveLineIdx(lineIdx());
                                      store.splitLineAtToken(lineIdx(), tokIdx());
                                    }}
                                    title="Split into a new line starting here"
                                  >
                                    ✂
                                  </button>
                                </Show>
                                <button
                                  class="tok-act-btn"
                                  disabled={isLastLine()}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    store.setActiveLineIdx(lineIdx());
                                    store.pushSyllableToNextLine(lineIdx(), tokIdx());
                                  }}
                                  title="Push to next line"
                                >
                                  ⬇
                                </button>
                                <button
                                  class="tok-act-btn"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    store.setActiveLineIdx(lineIdx());
                                    store.openCommentModal(tokIdx());
                                  }}
                                  title="Add / Edit Comment (C)"
                                >
                                  💬
                                </button>
                              </div>

                              <Show when={tok.comment}>
                                <span
                                  class="tok-comment-dot"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    store.setActiveLineIdx(lineIdx());
                                    store.openCommentModal(tokIdx());
                                  }}
                                  title={`Note: ${tok.comment}`}
                                >
                                  💬
                                </span>
                              </Show>

                              <span class="tok-en">{tok.en || tok.text}</span>
                              <span class="tok-py">{tok.pinyin || tok.text}</span>
                              <span class="tok-char">{tok.text}</span>
                            </div>
                          );
                        }}
                      </For>
                    </div>
                  </td>
                </tr>
              );
            }}
          </For>
        </tbody>
      </table>
    </div>
  );
};
