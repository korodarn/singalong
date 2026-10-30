import { createSignal, createRoot } from 'solid-js';
import { ProjectData, ProjectSummary, Line, Token } from '../types';
import { fetchProjects, fetchProject, saveProject } from '../api';

export interface ToastItem {
  id: number;
  message: string;
}

export function fmtMs(ms: number): string {
  if (ms < 0 || isNaN(ms)) ms = 0;
  const totalSec = ms / 1000.0;
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${String(m).padStart(2, '0')}:${s.toFixed(2).padStart(5, '0')}`;
}

function recalcLineBounds(ln: Line) {
  if (!ln || !ln.tokens) return;
  const active = ln.tokens.filter((t) => !t.is_deleted);
  if (active.length > 0) {
    ln.start = active[0].start;
    ln.end = active[active.length - 1].end;
  }
}

function createAppStore() {
  const [projects, setProjects] = createSignal<ProjectSummary[]>([]);
  const [currentVid, setCurrentVid] = createSignal<string>('');
  const [headerInputVid, setHeaderInputVid] = createSignal<string>('');
  const [projectData, setProjectData] = createSignal<ProjectData | null>(null);
  const [lines, setLines] = createSignal<Line[]>([]);
  const [activeLineIdx, setActiveLineIdx] = createSignal<number>(0);

  // Selection state within activeLineIdx
  const [selectedTokenIndices, setSelectedTokenIndices] = createSignal<number[]>([]);
  const [lastSelectedTokenIdx, setLastSelectedTokenIdx] = createSignal<number | null>(null);

  // Playback & waveform state
  const [currentTimeMs, setCurrentTimeMs] = createSignal<number>(0);
  const [totalDurationMs, setTotalDurationMs] = createSignal<number>(0);
  const [isPlaying, setIsPlaying] = createSignal<boolean>(false);
  const [followPlayhead, setFollowPlayhead] = createSignal<boolean>(true);
  const [stemMode, setStemMode] = createSignal<'vocals' | 'mix'>('vocals');
  const [tierMode, setTierMode] = createSignal<'3' | '2' | '1'>('3');
  const [playbackRate, setPlaybackRate] = createSignal<number>(1.0);
  const [waveformZoom, setWaveformZoom] = createSignal<number>(70);

  // Tap-Along state
  const [isTapMode, setIsTapMode] = createSignal<boolean>(false);
  const [tapTokenIdx, setTapTokenIdx] = createSignal<number>(0);
  let tapBackupTokens: Token[] | null = null;

  // Save & UI status
  const [isSaving, setIsSaving] = createSignal<boolean>(false);
  const [statusMsg, setStatusMsg] = createSignal<string>('');
  const [toasts, setToasts] = createSignal<ToastItem[]>([]);
  let nextToastId = 1;

  // Modals
  const [showIngestModal, setShowIngestModal] = createSignal<boolean>(false);
  const [showRenderModal, setShowRenderModal] = createSignal<boolean>(false);
  const [showCommentModal, setShowCommentModal] = createSignal<boolean>(false);
  const [editingToken, setEditingToken] = createSignal<{ lineIdx: number; tokenIdx: number; token: Token } | null>(null);

  // Undo history
  const undoStack: string[] = [];

  function addToast(message: string) {
    const id = nextToastId++;
    setToasts((prev) => [...prev, { id, message }]);
    setStatusMsg(message);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 2800);
  }

  function pushUndo() {
    undoStack.push(JSON.stringify(lines()));
    if (undoStack.length > 40) undoStack.shift();
  }

  function undo() {
    if (undoStack.length === 0) {
      addToast('Nothing to undo');
      return;
    }
    const prev = undoStack.pop();
    if (prev) {
      const parsed = JSON.parse(prev) as Line[];
      setLines(parsed);
      if (activeLineIdx() >= parsed.length) {
        setActiveLineIdx(Math.max(0, parsed.length - 1));
      }
      clearTokenSelection();
      addToast('↩ Undid last change');
    }
  }

  // Selection helpers
  function clearTokenSelection() {
    setSelectedTokenIndices([]);
    setLastSelectedTokenIdx(null);
  }

  function selectSingleToken(tokIdx: number) {
    setSelectedTokenIndices([tokIdx]);
    setLastSelectedTokenIdx(tokIdx);
  }

  function toggleSelectToken(tokIdx: number) {
    const cur = new Set(selectedTokenIndices());
    if (cur.has(tokIdx)) {
      cur.delete(tokIdx);
    } else {
      cur.add(tokIdx);
      setLastSelectedTokenIdx(tokIdx);
    }
    setSelectedTokenIndices(Array.from(cur).sort((a, b) => a - b));
  }

  function shiftSelectToken(tokIdx: number) {
    const anchor = lastSelectedTokenIdx();
    if (anchor === null) {
      selectSingleToken(tokIdx);
      return;
    }
    const low = Math.min(anchor, tokIdx);
    const high = Math.max(anchor, tokIdx);
    const cur = new Set(selectedTokenIndices());
    for (let i = low; i <= high; i++) {
      cur.add(i);
    }
    setSelectedTokenIndices(Array.from(cur).sort((a, b) => a - b));
  }

  function selectTokenRange(indices: number[]) {
    const cur = new Set(selectedTokenIndices());
    indices.forEach((i) => cur.add(i));
    const sorted = Array.from(cur).sort((a, b) => a - b);
    setSelectedTokenIndices(sorted);
    if (sorted.length > 0) {
      setLastSelectedTokenIdx(sorted[sorted.length - 1]);
    }
  }

  function selectAllTokensInLine(selectAll: boolean) {
    const curLine = lines()[activeLineIdx()];
    if (!curLine || !selectAll) {
      clearTokenSelection();
      return;
    }
    const all = curLine.tokens.map((_, i) => i);
    setSelectedTokenIndices(all);
    setLastSelectedTokenIdx(all.length > 0 ? all.length - 1 : null);
  }

  function selectLine(idx: number) {
    const lns = lines();
    if (idx < 0 || idx >= lns.length) return;
    if (activeLineIdx() !== idx) {
      setActiveLineIdx(idx);
      clearTokenSelection();
    }
  }

  async function loadProjectList() {
    try {
      const list = await fetchProjects();
      setProjects(list);
      return list;
    } catch (e: any) {
      console.error('Failed to load project list:', e);
      return [];
    }
  }

  async function selectProject(vid: string) {
    if (!vid) return;
    try {
      setStatusMsg(`Loading ${vid}...`);
      setHeaderInputVid(vid);
      const data = await fetchProject(vid);
      setProjectData(data);
      setCurrentVid(vid);
      setLines(data.lines || []);
      setActiveLineIdx(0);
      clearTokenSelection();
      setStemMode(data.has_vocals ? 'vocals' : 'mix');
      const savedTier = data.metadata?.tier_mode;
      if (savedTier === '1' || savedTier === 'single_tier') setTierMode('1');
      else if (savedTier === '2' || savedTier === 'two_tier') setTierMode('2');
      else setTierMode('3');
      setStatusMsg(`Loaded ${vid}`);
      window.history.replaceState(null, '', `?vid=${encodeURIComponent(vid)}`);
    } catch (e: any) {
      setStatusMsg(`Project '${vid}' not built yet — opening pipeline modal`);
      setHeaderInputVid(vid);
      setShowIngestModal(true);
    }
  }

  async function saveCurrentProject() {
    const vid = currentVid();
    if (!vid) return;
    try {
      setIsSaving(true);
      setStatusMsg('Saving changes...');
      const res = await saveProject(vid, lines());
      if (res.ok) {
        addToast('✅ Saved JSON & dictionary');
        if (res.lint && projectData()) {
          setProjectData({ ...projectData()!, lint: res.lint });
        }
      } else {
        addToast(`Save error: ${res.error}`);
      }
    } catch (e: any) {
      addToast(`Save error: ${e.message}`);
    } finally {
      setIsSaving(false);
    }
  }

  function updateActiveLineFromTime(timeMs: number) {
    setCurrentTimeMs(timeMs);
    if (!followPlayhead() || isTapMode() || !isPlaying()) return;
    const lns = lines();
    const curIdx = activeLineIdx();
    const curLn = lns[curIdx];
    const inCur = curLn && timeMs >= curLn.start - 200 && timeMs <= curLn.end + 250;
    if (!inCur) {
      const matchIdx = lns.findIndex((ln) => timeMs >= ln.start && timeMs <= ln.end);
      if (matchIdx !== -1 && matchIdx !== curIdx) {
        selectLine(matchIdx);
      }
    }
  }

  function toggleTokenDeleted(lineIdx: number, tokenIdx: number) {
    pushUndo();
    const lns = JSON.parse(JSON.stringify(lines())) as Line[];
    if (!lns[lineIdx]) return;
    if (lineIdx !== activeLineIdx()) {
      setActiveLineIdx(lineIdx);
      clearTokenSelection();
    }

    const sel = selectedTokenIndices();
    const targets = lineIdx === activeLineIdx() && sel.includes(tokenIdx) && sel.length > 1 ? sel : [tokenIdx];
    const newDelState = !lns[lineIdx].tokens[tokenIdx].is_deleted;
    targets.forEach((idx) => {
      if (lns[lineIdx].tokens[idx]) {
        lns[lineIdx].tokens[idx].is_deleted = newDelState;
      }
    });

    recalcLineBounds(lns[lineIdx]);
    setLines(lns);
    addToast(newDelState ? `Deleted ${targets.length} syllable(s)` : `Restored ${targets.length} syllable(s)`);
  }

  function toggleLineBacking(lineIdx: number) {
    pushUndo();
    const lns = JSON.parse(JSON.stringify(lines())) as Line[];
    if (!lns[lineIdx]) return;
    lns[lineIdx].is_backing = !lns[lineIdx].is_backing;
    setLines(lns);
    addToast(`Line #${lineIdx + 1} set to ${lns[lineIdx].is_backing ? 'Backing (Cyan)' : 'Lead (Gold)'}`);
  }

  function splitLineAtToken(lineIdx: number, tokenIdx: number) {
    const lns = JSON.parse(JSON.stringify(lines())) as Line[];
    const curLine = lns[lineIdx];
    if (!curLine || tokenIdx <= 0 || tokenIdx >= curLine.tokens.length) {
      addToast('Select a syllable after the first one in the line to split');
      return;
    }

    pushUndo();
    const firstTokens = curLine.tokens.slice(0, tokenIdx);
    const secondTokens = curLine.tokens.slice(tokenIdx);

    const lineA: Line = {
      start: firstTokens[0].start,
      end: firstTokens[firstTokens.length - 1].end,
      is_backing: curLine.is_backing,
      tokens: firstTokens,
    };
    recalcLineBounds(lineA);

    const lineB: Line = {
      start: secondTokens[0].start,
      end: secondTokens[secondTokens.length - 1].end,
      is_backing: curLine.is_backing,
      tokens: secondTokens,
    };
    recalcLineBounds(lineB);

    lns.splice(lineIdx, 1, lineA, lineB);
    setLines(lns);
    setActiveLineIdx(lineIdx + 1);
    clearTokenSelection();
    addToast(`✂ Split into new Line #${lineIdx + 2}`);
  }

  function pushSyllableToPrevLine(lineIdx: number, tokIdx?: number) {
    const lns = JSON.parse(JSON.stringify(lines())) as Line[];
    if (lineIdx <= 0 || !lns[lineIdx] || !lns[lineIdx - 1]) {
      addToast('No previous line to push into');
      return;
    }

    const curLine = lns[lineIdx];
    const prevLine = lns[lineIdx - 1];

    let moveIndices: number[] = [];
    const sel = selectedTokenIndices();
    if (lineIdx === activeLineIdx() && sel.length > 0 && (tokIdx === undefined || sel.includes(tokIdx))) {
      moveIndices = [...sel].sort((a, b) => a - b);
    } else if (tokIdx !== undefined) {
      moveIndices = [tokIdx];
    } else {
      moveIndices = [0];
    }

    const movingTokens = moveIndices.map((i) => curLine.tokens[i]).filter(Boolean);
    if (!movingTokens.length) return;

    pushUndo();
    const indicesSet = new Set(moveIndices);
    curLine.tokens = curLine.tokens.filter((_, i) => !indicesSet.has(i));

    prevLine.tokens = (prevLine.tokens || []).concat(movingTokens);
    prevLine.tokens.sort((a, b) => (a.start || 0) - (b.start || 0));
    recalcLineBounds(prevLine);

    clearTokenSelection();

    if (curLine.tokens.length === 0) {
      lns.splice(lineIdx, 1);
      setLines(lns);
      setActiveLineIdx(Math.max(0, lineIdx - 1));
    } else {
      recalcLineBounds(curLine);
      setLines(lns);
    }
    addToast(`⬆ Pushed ${movingTokens.length} syllable(s) to Line #${lineIdx}`);
  }

  function pushSyllableToNextLine(lineIdx: number, tokIdx?: number) {
    const lns = JSON.parse(JSON.stringify(lines())) as Line[];
    if (lineIdx >= lns.length - 1 || !lns[lineIdx] || !lns[lineIdx + 1]) {
      addToast('No next line to push into');
      return;
    }

    const curLine = lns[lineIdx];
    const nextLine = lns[lineIdx + 1];

    let moveIndices: number[] = [];
    const sel = selectedTokenIndices();
    if (lineIdx === activeLineIdx() && sel.length > 0 && (tokIdx === undefined || sel.includes(tokIdx))) {
      moveIndices = [...sel].sort((a, b) => a - b);
    } else if (tokIdx !== undefined) {
      moveIndices = [tokIdx];
    } else {
      moveIndices = [curLine.tokens.length - 1];
    }

    const movingTokens = moveIndices.map((i) => curLine.tokens[i]).filter(Boolean);
    if (!movingTokens.length) return;

    pushUndo();
    const indicesSet = new Set(moveIndices);
    curLine.tokens = curLine.tokens.filter((_, i) => !indicesSet.has(i));

    nextLine.tokens = movingTokens.concat(nextLine.tokens || []);
    nextLine.tokens.sort((a, b) => (a.start || 0) - (b.start || 0));
    recalcLineBounds(nextLine);

    clearTokenSelection();

    if (curLine.tokens.length === 0) {
      lns.splice(lineIdx, 1);
      setLines(lns);
      setActiveLineIdx(Math.min(lineIdx, lns.length - 1));
    } else {
      recalcLineBounds(curLine);
      setLines(lns);
    }
    addToast(`⬇ Pushed ${movingTokens.length} syllable(s) to Line #${lineIdx + 2}`);
  }

  function mergeWithNextLine(lineIdx: number) {
    const lns = JSON.parse(JSON.stringify(lines())) as Line[];
    if (lineIdx < 0 || lineIdx >= lns.length - 1) {
      addToast('No next line to merge with');
      return;
    }
    pushUndo();
    const curLine = lns[lineIdx];
    const nextLine = lns[lineIdx + 1];
    curLine.tokens = (curLine.tokens || []).concat(nextLine.tokens || []);
    curLine.tokens.sort((a, b) => (a.start || 0) - (b.start || 0));
    recalcLineBounds(curLine);
    lns.splice(lineIdx + 1, 1);
    setLines(lns);
    clearTokenSelection();
    addToast(`🔗 Merged Line #${lineIdx + 2} into Line #${lineIdx + 1}`);
  }

  function nudgeActiveLine(deltaMs: number) {
    const idx = activeLineIdx();
    const lns = JSON.parse(JSON.stringify(lines())) as Line[];
    const ln = lns[idx];
    if (!ln) return;
    pushUndo();
    (ln.tokens || []).forEach((t) => {
      t.start = Math.max(0, t.start + deltaMs);
      t.end = Math.max(t.start + 20, t.end + deltaMs);
    });
    recalcLineBounds(ln);
    setLines(lns);
    addToast(`Nudged Line #${idx + 1} by ${deltaMs > 0 ? '+' : ''}${deltaMs}ms`);
  }

  function realignLineUniform() {
    const idx = activeLineIdx();
    const lns = JSON.parse(JSON.stringify(lines())) as Line[];
    const ln = lns[idx];
    if (!ln) return;
    const toks = (ln.tokens || []).filter((t) => !t.is_deleted);
    if (!toks.length) return;
    pushUndo();
    const start = toks[0].start;
    const end = toks[toks.length - 1].end;
    const durPerTok = Math.max(60, Math.floor((end - start) / toks.length));
    toks.forEach((t, i) => {
      t.start = start + i * durPerTok;
      t.end = i === toks.length - 1 ? Math.max(t.start + 60, end) : t.start + durPerTok;
    });
    recalcLineBounds(ln);
    setLines(lns);
    addToast(`⚖ Evenly distributed ${toks.length} syllables across Line #${idx + 1}`);
  }

  // Comments (C key & button)
  function openCommentModal(tokIdx?: number) {
    const curLine = lines()[activeLineIdx()];
    if (!curLine || !curLine.tokens.length) return;

    if (tokIdx !== undefined) {
      if (!selectedTokenIndices().includes(tokIdx)) {
        selectSingleToken(tokIdx);
      }
    } else if (selectedTokenIndices().length === 0) {
      const firstActive = curLine.tokens.findIndex((t) => !t.is_deleted);
      selectSingleToken(firstActive >= 0 ? firstActive : 0);
    }
    setShowCommentModal(true);
  }

  function saveCommentOnSelected(commentText: string) {
    const idx = activeLineIdx();
    const lns = JSON.parse(JSON.stringify(lines())) as Line[];
    const ln = lns[idx];
    if (!ln) return;
    const indices = selectedTokenIndices();
    if (!indices.length) return;

    pushUndo();
    const trimmed = commentText.trim();
    indices.forEach((tIdx) => {
      if (ln.tokens[tIdx]) {
        if (trimmed) {
          ln.tokens[tIdx].comment = trimmed;
        } else {
          delete ln.tokens[tIdx].comment;
        }
      }
    });
    setLines(lns);
    setShowCommentModal(false);
    addToast(trimmed ? `💬 Saved comment on ${indices.length} syllable(s)` : `🗑️ Cleared comment on ${indices.length} syllable(s)`);
  }

  function updateTokenPhonetics(lineIdx: number, tokenIdx: number, text: string, pinyin: string, en: string) {
    pushUndo();
    const lns = JSON.parse(JSON.stringify(lines())) as Line[];
    const tok = lns[lineIdx].tokens[tokenIdx];
    if (text) tok.text = text;
    tok.pinyin = pinyin;
    tok.en = en;
    setLines(lns);
    addToast(`Updated phonetics for '${tok.text}'`);
  }

  function updateTokenTimes(lineIdx: number, tokenIdx: number, startMs: number, endMs: number) {
    const lns = JSON.parse(JSON.stringify(lines())) as Line[];
    const tok = lns[lineIdx].tokens[tokenIdx];
    tok.start = Math.max(0, Math.round(startMs));
    tok.end = Math.max(tok.start + 20, Math.round(endMs));
    recalcLineBounds(lns[lineIdx]);
    setLines(lns);
  }

  function shiftTokens(lineIdx: number, tokenIndices: number[], deltaMs: number, recordUndo = true) {
    if (!tokenIndices.length || deltaMs === 0) return;
    if (recordUndo) pushUndo();
    const lns = JSON.parse(JSON.stringify(lines())) as Line[];
    const curLine = lns[lineIdx];
    if (!curLine) return;
    const indicesSet = new Set(tokenIndices);
    curLine.tokens.forEach((t, i) => {
      if (indicesSet.has(i)) {
        t.start = Math.max(0, t.start + deltaMs);
        t.end = Math.max(t.start + 20, t.end + deltaMs);
      }
    });
    recalcLineBounds(curLine);
    setLines(lns);
  }

  function adjustTokenDuration(lineIdx: number, tokenIndices: number[], deltaEndMs: number, recordUndo = true) {
    if (!tokenIndices.length || deltaEndMs === 0) return;
    if (recordUndo) pushUndo();
    const lns = JSON.parse(JSON.stringify(lines())) as Line[];
    const curLine = lns[lineIdx];
    if (!curLine) return;
    const indicesSet = new Set(tokenIndices);
    curLine.tokens.forEach((t, i) => {
      if (indicesSet.has(i)) {
        t.end = Math.max(t.start + 25, t.end + deltaEndMs);
      }
    });
    recalcLineBounds(curLine);
    setLines(lns);
  }

  // Tap-Along helpers with backup/cancel
  function startTapAlongSession() {
    const curLine = lines()[activeLineIdx()];
    if (!curLine) return false;
    const activeToks = curLine.tokens.filter((t) => !t.is_deleted);
    if (!activeToks.length) return false;

    tapBackupTokens = JSON.parse(JSON.stringify(curLine.tokens));
    setIsTapMode(true);
    setTapTokenIdx(0);
    return true;
  }

  function cancelTapAlongSession() {
    if (!isTapMode()) return;
    if (tapBackupTokens) {
      const lns = JSON.parse(JSON.stringify(lines())) as Line[];
      if (lns[activeLineIdx()]) {
        lns[activeLineIdx()].tokens = tapBackupTokens;
        recalcLineBounds(lns[activeLineIdx()]);
        setLines(lns);
      }
    }
    tapBackupTokens = null;
    setIsTapMode(false);
    setTapTokenIdx(0);
    addToast('Cancelled Tap-Along');
  }

  function finishTapAlongSession() {
    tapBackupTokens = null;
    setIsTapMode(false);
    setTapTokenIdx(0);
    addToast('✅ Tap-Along timing applied');
  }

  return {
    projects,
    setProjects,
    currentVid,
    setCurrentVid,
    headerInputVid,
    setHeaderInputVid,
    projectData,
    setProjectData,
    lines,
    setLines,
    activeLineIdx,
    setActiveLineIdx: selectLine,
    selectedTokenIndices,
    setSelectedTokenIndices,
    lastSelectedTokenIdx,
    clearTokenSelection,
    selectSingleToken,
    toggleSelectToken,
    shiftSelectToken,
    selectTokenRange,
    selectAllTokensInLine,
    currentTimeMs,
    setCurrentTimeMs,
    totalDurationMs,
    setTotalDurationMs,
    isPlaying,
    setIsPlaying,
    followPlayhead,
    setFollowPlayhead,
    stemMode,
    setStemMode,
    tierMode,
    setTierMode,
    playbackRate,
    setPlaybackRate,
    waveformZoom,
    setWaveformZoom,
    isTapMode,
    setIsTapMode,
    tapTokenIdx,
    setTapTokenIdx,
    startTapAlongSession,
    cancelTapAlongSession,
    finishTapAlongSession,
    isSaving,
    statusMsg,
    setStatusMsg,
    toasts,
    addToast,
    showIngestModal,
    setShowIngestModal,
    showRenderModal,
    setShowRenderModal,
    showCommentModal,
    setShowCommentModal,
    openCommentModal,
    saveCommentOnSelected,
    editingToken,
    setEditingToken,
    pushUndo,
    undo,
    loadProjectList,
    selectProject,
    saveCurrentProject,
    updateActiveLineFromTime,
    toggleTokenDeleted,
    toggleLineBacking,
    splitLineAtToken,
    pushSyllableToPrevLine,
    pushSyllableToNextLine,
    mergeWithNextLine,
    nudgeActiveLine,
    realignLineUniform,
    updateTokenPhonetics,
    updateTokenTimes,
    shiftTokens,
    adjustTokenDuration,
  };
}

export const store = createRoot(createAppStore);
