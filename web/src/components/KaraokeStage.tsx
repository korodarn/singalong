import { Component, For, Show, createSignal, onMount } from 'solid-js';
import { store, fmtMs } from '../state/store';
import { Token } from '../types';

export const KaraokeStage: Component<{
  onPlayBounds: (startMs: number, endMs?: number) => void;
}> = (props) => {
  const [stageHeight, setStageHeight] = createSignal<number>(195);

  onMount(() => {
    const saved = localStorage.getItem('singalong_preview_height');
    if (saved) {
      const h = parseInt(saved, 10);
      if (!isNaN(h) && h >= 100 && h <= 500) setStageHeight(h);
    }
  });

  const startResize = (e: MouseEvent) => {
    e.preventDefault();
    const startY = e.clientY;
    const startH = stageHeight();

    const onMove = (ev: MouseEvent) => {
      const nextH = Math.max(105, Math.min(480, startH + (ev.clientY - startY)));
      setStageHeight(nextH);
    };
    const onUp = () => {
      localStorage.setItem('singalong_preview_height', String(stageHeight()));
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  const currentLine = () => {
    const lns = store.lines();
    const idx = store.activeLineIdx();
    return lns[idx] || null;
  };

  const hasCjk = () => {
    const ln = currentLine();
    if (!ln) return true;
    return (ln.tokens || []).some((t) => /[\u4e00-\u9fff\uac00-\ud7af\u3040-\u30ff]/.test(t.text));
  };

  const getWipePct = (tok: Token) => {
    const t = store.currentTimeMs();
    if (t <= tok.start) return 0;
    if (t >= tok.end) return 100;
    return ((t - tok.start) / Math.max(1, tok.end - tok.start)) * 100;
  };

  const isCurrentActiveToken = (tok: Token) => {
    const t = store.currentTimeMs();
    return t >= tok.start && t <= tok.end;
  };

  const tapStatusText = () => {
    const ln = currentLine();
    if (!ln) return '';
    const activeToks = ln.tokens.filter((t) => !t.is_deleted);
    const idx = store.tapTokenIdx();
    const cur = activeToks[idx];
    if (!cur) return 'Done!';
    return `Syllable ${idx + 1}/${activeToks.length}: [${cur.text}] (${cur.pinyin || ''})`;
  };

  return (
    <>
      <section class="preview-stage" style={{ height: `${stageHeight()}px` }}>
        <div class="stage-top-meta">
          <Show when={currentLine()}>
            {(line) => (
              <>
                <span
                  class={`lane-badge ${line().is_backing ? 'backing' : 'lead'}`}
                  onClick={() => store.toggleLineBacking(store.activeLineIdx())}
                  style={{ cursor: 'pointer' }}
                  title="Click or press [B] to toggle Lead / Backing track"
                >
                  {line().is_backing ? '👥 BACKING VOCALS (CYAN)' : '🎤 LEAD VOCALS (GOLD)'}
                </span>
                <span class="meta-tag">
                  Line #{store.activeLineIdx() + 1} / {store.lines().length} • {fmtMs(line().start)} → {fmtMs(line().end)}
                </span>
                <span class="stage-hint">
                  Click: Select/Play • Shift+Click: Range • Alt+Click: Delete • [C]: Comment • Double-Click: Phonetics
                </span>
              </>
            )}
          </Show>
        </div>

        <div class="stage-viewport">
          <Show when={currentLine()} fallback={<div class="stage-placeholder">No line selected or project empty</div>}>
            {(line) => {
              const isFirstLine = () => store.activeLineIdx() === 0;
              const isLastLine = () => store.activeLineIdx() >= store.lines().length - 1;
              return (
                <div class={`karaoke-line ${line().is_backing ? 'is-backing' : 'is-lead'} ${!hasCjk() ? 'pure-english' : ''}`}>
                  <For each={line().tokens}>
                    {(tok, idx) => {
                      const pct = () => getWipePct(tok);
                      const isSelected = () => store.selectedTokenIndices().includes(idx());
                      return (
                        <div
                          class={`syllable-col ${tok.space ? 'has-space' : ''} ${tok.is_deleted ? 'deleted' : ''} ${
                            isCurrentActiveToken(tok) ? 'active-singing' : ''
                          } ${isSelected() ? 'selected' : ''}`}
                          onClick={(e) => {
                            if (e.altKey) {
                              e.stopPropagation();
                              store.toggleTokenDeleted(store.activeLineIdx(), idx());
                              return;
                            }
                            if (e.shiftKey) {
                              e.stopPropagation();
                              store.shiftSelectToken(idx());
                              return;
                            }
                            if (e.ctrlKey || e.metaKey) {
                              e.stopPropagation();
                              store.toggleSelectToken(idx());
                              return;
                            }
                            store.selectSingleToken(idx());
                            props.onPlayBounds(tok.start, tok.end + 80);
                          }}
                          onDblClick={(e) => {
                            e.stopPropagation();
                            store.setEditingToken({ lineIdx: store.activeLineIdx(), tokenIdx: idx(), token: tok });
                          }}
                          title={`Click: Select & Play | Shift+Click: Range | [C]: Comment | Double-Click: Phonetics${
                            tok.comment ? ' | Note: ' + tok.comment : ''
                          }`}
                        >
                          <Show when={tok.comment}>
                            <span
                              class="karaoke-col-comment-badge"
                              onClick={(e) => {
                                e.stopPropagation();
                                store.openCommentModal(idx());
                              }}
                              title={tok.comment}
                            >
                              💬
                            </span>
                          </Show>

                          <div class="col-hover-actions">
                            <button
                              class="btn-col-act"
                              disabled={isFirstLine()}
                              onClick={(e) => {
                                e.stopPropagation();
                                store.pushSyllableToPrevLine(store.activeLineIdx(), idx());
                              }}
                              title="Push syllable(s) to previous line"
                            >
                              ⬆
                            </button>
                            <Show when={idx() > 0}>
                              <button
                                class="btn-col-act"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  store.splitLineAtToken(store.activeLineIdx(), idx());
                                }}
                                title="Split into a new line starting at this syllable"
                              >
                                ✂
                              </button>
                            </Show>
                            <button
                              class="btn-col-act"
                              disabled={isLastLine()}
                              onClick={(e) => {
                                e.stopPropagation();
                                store.pushSyllableToNextLine(store.activeLineIdx(), idx());
                              }}
                              title="Push syllable(s) to next line"
                            >
                              ⬇
                            </button>
                          </div>

                          <Show when={hasCjk()}>
                            <span class="tier-en" style={{ '--wipe-pct': `${pct()}%` }}>
                              {tok.en || tok.text}
                            </span>
                            <span class="tier-py" style={{ '--wipe-pct': `${pct()}%` }}>
                              {tok.pinyin || tok.text}
                            </span>
                          </Show>
                          <span class="tier-hz" style={{ '--wipe-pct': `${pct()}%` }}>
                            {tok.text}
                          </span>
                        </div>
                      );
                    }}
                  </For>
                </div>
              );
            }}
          </Show>
        </div>

        <Show when={store.isTapMode()}>
          <div class="tap-along-banner active">
            <span>🥁 TAP-ALONG (0.75x):</span>
            <span style={{ 'font-family': 'var(--font-mono)' }}>{tapStatusText()}</span>
            <span style={{ 'font-size': '11px', opacity: 0.9 }}>Tap [SPACE] on each syllable onset • [ESC] to cancel</span>
          </div>
        </Show>
      </section>
      <div class="resizer-gutter" onMouseDown={startResize} title="Drag to resize Karaoke Stage height" />
    </>
  );
};
