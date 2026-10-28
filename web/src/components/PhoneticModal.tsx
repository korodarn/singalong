import { Component, createEffect, createSignal, Show } from 'solid-js';
import { store } from '../state/store';
import { updateCustomDict } from '../api';

export const PhoneticModal: Component = () => {
  const [textVal, setTextVal] = createSignal('');
  const [pyVal, setPyVal] = createSignal('');
  const [enVal, setEnVal] = createSignal('');
  const [lang, setLang] = createSignal('zh');
  const [saveToGlobal, setSaveToGlobal] = createSignal(true);
  let pinyinInput!: HTMLInputElement;

  const current = () => store.editingToken();

  createEffect(() => {
    const edit = current();
    if (edit) {
      setTextVal(edit.token.text || '');
      setPyVal(edit.token.pinyin || '');
      setEnVal(edit.token.en || '');
      if (/[\uac00-\ud7af]/.test(edit.token.text)) {
        setLang('ko');
      } else if (/[\u3040-\u30ff]/.test(edit.token.text)) {
        setLang('ja');
      } else {
        setLang('zh');
      }
      setTimeout(() => pinyinInput?.focus(), 40);
    }
  });

  const handleSave = async () => {
    const edit = current();
    if (!edit) return;

    const txt = textVal().trim() || edit.token.text;
    const py = pyVal().trim();
    const en = enVal().trim();

    store.updateTokenPhonetics(edit.lineIdx, edit.tokenIdx, txt, py, en);

    if (saveToGlobal() && txt) {
      try {
        await updateCustomDict({
          [lang()]: { [txt]: { pinyin: py, en: en } },
        });
      } catch (e) {
        console.error('Failed to update dictionary:', e);
      }
    }
    store.setEditingToken(null);
  };

  return (
    <Show when={current()}>
      {(edit) => (
        <div class="modal-backdrop" onClick={() => store.setEditingToken(null)}>
          <div class="modal-card" onClick={(e) => e.stopPropagation()}>
            <div class="modal-header">
              <h3>✏️ Edit Syllable Phonetics: "{edit().token.text}"</h3>
              <button class="btn-close" onClick={() => store.setEditingToken(null)}>✕</button>
            </div>
            <div
              class="modal-body"
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  handleSave();
                } else if (e.key === 'Escape') {
                  store.setEditingToken(null);
                }
              }}
            >
              <label class="form-label">
                Character / Token Text:
                <input
                  class="form-input"
                  value={textVal()}
                  onInput={(e) => setTextVal(e.currentTarget.value)}
                />
              </label>
              <label class="form-label">
                Pinyin / Romanization (Tier 2):
                <input
                  ref={pinyinInput}
                  class="form-input"
                  value={pyVal()}
                  onInput={(e) => setPyVal(e.currentTarget.value)}
                />
              </label>
              <label class="form-label">
                US-English Phonetic Respelling (Tier 3):
                <input
                  class="form-input"
                  value={enVal()}
                  onInput={(e) => setEnVal(e.currentTarget.value)}
                />
              </label>
              <label class="form-label">
                Dictionary Language:
                <select class="form-input" value={lang()} onChange={(e) => setLang(e.currentTarget.value)}>
                  <option value="zh">Chinese (zh)</option>
                  <option value="ko">Korean (ko)</option>
                  <option value="ja">Japanese (ja)</option>
                </select>
              </label>
              <label class="form-checkbox">
                <input
                  type="checkbox"
                  checked={saveToGlobal()}
                  onChange={(e) => setSaveToGlobal(e.currentTarget.checked)}
                />
                Save to global custom_dict.json (persists across future runs)
              </label>
            </div>
            <div class="modal-footer">
              <button class="btn" onClick={() => store.setEditingToken(null)}>Cancel</button>
              <button class="btn btn-primary" onClick={handleSave}>Apply Override</button>
            </div>
          </div>
        </div>
      )}
    </Show>
  );
};
