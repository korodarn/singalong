import { Component, createEffect, createSignal, For, Show } from 'solid-js';
import { store, fmtMs } from '../state/store';

const QUICK_TAGS = [
  { label: '⚡ ASR Missed Onset', value: 'ASR missed onset' },
  { label: '⚡ Boundary Bleed', value: 'Boundary bleed' },
  { label: '⚡ Reverb Ghost Tail', value: 'Reverb ghost tail' },
  { label: '⚡ LRC Mismatch', value: 'LRC text mismatch' },
  { label: '⚡ Dropped Consonant', value: 'Consonant unvoiced/dropped' },
  { label: '⚡ Runaway Hold', value: 'Hold sustained too long' },
  { label: '⚡ Line Split Needed', value: 'Line split needed' },
  { label: '⚡ Backing Overlap', value: 'Backing vocal overlap' },
];

export const CommentModal: Component = () => {
  const [text, setText] = createSignal('');
  let textareaRef!: HTMLTextAreaElement;

  const selectedInfo = () => {
    const lineIdx = store.activeLineIdx();
    const ln = store.lines()[lineIdx];
    if (!ln) return null;
    const indices = store.selectedTokenIndices();
    const selToks = indices.map((i) => ln.tokens[i]).filter(Boolean);
    if (!selToks.length) return null;

    const chars = selToks.map((t) => t.text).join(' ');
    const pinyins = selToks.map((t) => t.pinyin || '').filter(Boolean).join(' ');
    const minStart = Math.min(...selToks.map((t) => t.start || 0));
    const maxEnd = Math.max(...selToks.map((t) => t.end || 0));
    const existing = selToks.map((t) => t.comment).filter(Boolean);

    return {
      lineNumber: lineIdx + 1,
      count: selToks.length,
      chars,
      pinyins,
      minStart,
      maxEnd,
      hasExisting: existing.length > 0,
      firstExisting: existing[0] || '',
    };
  };

  createEffect(() => {
    if (store.showCommentModal()) {
      const info = selectedInfo();
      setText(info?.firstExisting || '');
      setTimeout(() => textareaRef?.focus(), 40);
    }
  });

  const insertTag = (tag: string) => {
    const cur = text().trim();
    setText(cur.length > 0 ? `${cur}; ${tag}` : tag);
    textareaRef?.focus();
  };

  const handleSave = () => {
    store.saveCommentOnSelected(text());
  };

  const handleClear = () => {
    store.saveCommentOnSelected('');
  };

  return (
    <Show when={store.showCommentModal() && selectedInfo()}>
      {(info) => (
        <div class="modal-backdrop" onClick={() => store.setShowCommentModal(false)}>
          <div class="modal-card modal-large" onClick={(e) => e.stopPropagation()}>
            <div class="modal-header">
              <h3>💬 Add Analysis Comment for Syllable Selection</h3>
              <button class="btn-close" onClick={() => store.setShowCommentModal(false)}>✕</button>
            </div>

            <div class="modal-body">
              <div class="comment-target-box">
                <div style={{ display: 'flex', 'justify-content': 'space-between', 'align-items': 'center', 'margin-bottom': '4px' }}>
                  <span style={{ 'font-weight': 700, color: 'var(--accent-lead)' }}>
                    Line #{info().lineNumber} • {info().count} Syllable{info().count > 1 ? 's' : ''} Selected
                  </span>
                  <span style={{ 'font-family': 'var(--font-mono)', color: 'var(--text-muted)', 'font-size': '11px' }}>
                    {fmtMs(info().minStart)} → {fmtMs(info().maxEnd)} ({info().maxEnd - info().minStart}ms)
                  </span>
                </div>
                <div style={{ 'font-family': 'var(--font-cjk)', 'font-size': '16px', 'font-weight': 700, color: '#fff' }}>
                  {info().chars}{' '}
                  <Show when={info().pinyins}>
                    <span style={{ 'font-family': 'var(--font-mono)', 'font-size': '12px', color: 'var(--accent-lead)', 'font-weight': 'normal', 'margin-left': '8px' }}>
                      [{info().pinyins}]
                    </span>
                  </Show>
                </div>
              </div>

              <div class="form-label">
                <span>Quick Analysis Feedback Tags (Click to insert):</span>
                <div class="tag-buttons-wrap">
                  <For each={QUICK_TAGS}>
                    {(t) => (
                      <button type="button" class="btn btn-sm" onClick={() => insertTag(t.value)}>
                        {t.label}
                      </button>
                    )}
                  </For>
                </div>
              </div>

              <label class="form-label">
                <span>Analysis Direction / Notes (to guide automated improvements):</span>
                <textarea
                  ref={textareaRef}
                  rows={4}
                  class="form-textarea"
                  placeholder="Add notes explaining where the automated alignment was off and what needs improvement..."
                  value={text()}
                  onInput={(e) => setText(e.currentTarget.value)}
                  onKeyDown={(e) => {
                    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                      e.preventDefault();
                      handleSave();
                    } else if (e.key === 'Escape') {
                      e.preventDefault();
                      store.setShowCommentModal(false);
                    }
                  }}
                />
              </label>
            </div>

            <div class="modal-footer">
              <Show when={info().hasExisting}>
                <button class="btn btn-danger" style={{ 'margin-right': 'auto' }} onClick={handleClear}>
                  🗑️ Clear Comment
                </button>
              </Show>
              <button class="btn" onClick={() => store.setShowCommentModal(false)}>Cancel (Esc)</button>
              <button class="btn btn-primary" onClick={handleSave}>💾 Save Comment (Ctrl+Enter)</button>
            </div>
          </div>
        </div>
      )}
    </Show>
  );
};
