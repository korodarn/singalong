import { Component, For, Show, createEffect } from 'solid-js';
import { store, fmtMs } from '../state/store';

export const LyricTable: Component<{
  onSeekTo: (ms: number) => void;
  onPlayBounds: (startMs: number, endMs?: number) => void;
}> = (props) => {
  let tableContainerRef!: HTMLDivElement;

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

  return (
    <div ref={tableContainerRef} class="table-container">
      <table class="lyrics-table">
        <thead>
          <tr>
            <th style={{ width: '48px' }}>#</th>
            <th style={{ width: '155px' }}>Time Range</th>
            <th style={{ width: '95px' }}>Track</th>
            <th>
              Syllables, Pinyin & US Phonetics{' '}
              <span style={{ 'font-weight': 'normal', color: 'var(--text-dim)', 'margin-left': '8px' }}>
                (Click: Select/Play • Shift+Click: Range • Hover chip for ⬆ Prev / ✂ Split / ⬇ Next / 💬 Note • Double-Click: Edit Phonetics)
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
                  onClick={() => {
                    store.setActiveLineIdx(lineIdx());
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
                              onClick={(e) => {
                                e.stopPropagation();
                                if (store.activeLineIdx() !== lineIdx()) {
                                  store.setActiveLineIdx(lineIdx());
                                }
                                if (e.altKey) {
                                  store.toggleTokenDeleted(lineIdx(), tokIdx());
                                  return;
                                }
                                if (e.shiftKey) {
                                  store.shiftSelectToken(tokIdx());
                                  return;
                                }
                                if (e.ctrlKey || e.metaKey) {
                                  store.toggleSelectToken(tokIdx());
                                  return;
                                }
                                store.selectSingleToken(tokIdx());
                                props.onPlayBounds(tok.start, tok.end + 80);
                              }}
                              onDblClick={(e) => {
                                e.stopPropagation();
                                store.setActiveLineIdx(lineIdx());
                                store.setEditingToken({ lineIdx: lineIdx(), tokenIdx: tokIdx(), token: tok });
                              }}
                              title={`[${tok.start}ms..${tok.end}ms (${tok.end - tok.start}ms)] Click: Select & Play | Shift+Click: Range | Alt+Click: Delete | Double-Click: Edit Phonetics${
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
