import { Component, For, Show, createEffect, createSignal, onMount } from 'solid-js';
import { store, fmtMs } from '../state/store';
import { Token } from '../types';

export const KaraokeStage: Component<{
  onPlayBounds: (startMs: number, endMs?: number) => void;
}> = (props) => {
  const [stageHeight, setStageHeight] = createSignal<number>(195);
  let bgVideoRef: HTMLVideoElement | undefined;

  const meta = () => store.projectData()?.metadata;
  const hasBgMedia = () => Boolean(meta()?.bg_media_path || meta()?.bg_video_path);
  const bgStreamUrl = () => meta()?.bg_stream_url || (meta()?.bg_media_path ? `/api/bg-media/stream?path=${encodeURIComponent(meta()?.bg_media_path!.split('/').pop() || '')}` : '');
  const bgMediaType = () => meta()?.bg_media_type || (meta()?.bg_media_path?.match(/\.(jpe?g|png|webp|bmp)$/i) ? 'image' : 'video');
  const bgOffsetMs = () => meta()?.bg_offset_ms || 0;
  const bgDimPct = () => meta()?.bg_dim_pct ?? 35;

  onMount(() => {
    const saved = localStorage.getItem('singalong_preview_height');
    if (saved) {
      const h = parseInt(saved, 10);
      if (!isNaN(h) && h >= 100 && h <= 500) setStageHeight(h);
    }
  });

  createEffect(() => {
    if (!bgVideoRef || bgMediaType() !== 'video') return;
    const playing = store.isPlaying();
    const rate = store.playbackRate();
    bgVideoRef.playbackRate = rate;
    if (playing) {
      bgVideoRef.play().catch(() => {});
    } else {
      bgVideoRef.pause();
    }
  });

  createEffect(() => {
    if (!bgVideoRef || bgMediaType() !== 'video') return;
    const audioTimeMs = store.currentTimeMs();
    const offset = bgOffsetMs();
    const targetSec = Math.max(0, (audioTimeMs + offset) / 1000.0);
    if (Math.abs(bgVideoRef.currentTime - targetSec) > 0.25) {
      bgVideoRef.currentTime = targetSec;
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
        {/* Synchronized Background Video or Static Image Layer */}
        <Show when={hasBgMedia() && bgStreamUrl()}>
          <div class="stage-bg-container">
            <Show
              when={bgMediaType() === 'video'}
              fallback={
                <img
                  src={bgStreamUrl()}
                  alt="Background"
                  class="stage-bg-media"
                />
              }
            >
              <video
                ref={bgVideoRef}
                src={bgStreamUrl()}
                muted
                playsinline
                loop
                class="stage-bg-media"
              />
            </Show>
            <div
              class="stage-bg-dimmer"
              style={{ 'background-color': `rgba(0, 0, 0, ${(bgDimPct() / 100).toFixed(2)})` }}
            />
          </div>
        </Show>

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
                <button
                  class={`stage-bg-btn ${hasBgMedia() ? 'active' : ''}`}
                  onClick={() => store.setShowVideoBgModal(true)}
                  title="Set or adjust background video or image overlay"
                >
                  {hasBgMedia()
                    ? `🎬 BG: ${bgMediaType() === 'image' ? '🖼️ Image' : '🎥 Video'} (${bgDimPct()}%)`
                    : '🎬 Set Background'}
                </button>
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

                          <Show when={hasCjk() && store.tierMode() === '3'}>
                            <span class="tier-en" style={{ '--wipe-pct': `${pct()}%` }}>
                              {tok.en || tok.text}
                            </span>
                          </Show>
                          <Show when={hasCjk() && (store.tierMode() === '3' || store.tierMode() === '2')}>
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
