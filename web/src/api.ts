import { LyricsCandidate, LyricsValidation, ProjectData, ProjectSummary, ScriptDistribution } from './types';

export async function fetchProjects(): Promise<ProjectSummary[]> {
  const res = await fetch('/api/projects');
  if (!res.ok) throw new Error('Failed to fetch projects');
  return res.json();
}

export async function fetchProject(vid: string): Promise<ProjectData> {
  const res = await fetch(`/api/project/${encodeURIComponent(vid)}`);
  if (!res.ok) throw new Error(`Project ${vid} not found`);
  return res.json();
}

export async function searchLyrics(
  query: string,
  url?: string,
  target_lang?: string
): Promise<{ ok: boolean; candidates: LyricsCandidate[] }> {
  const res = await fetch('/api/lyrics/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, url, target_lang }),
  });
  if (!res.ok) throw new Error('Lyrics search request failed');
  return res.json();
}

export async function validateLyrics(
  raw_text: string,
  target_lang?: string
): Promise<{
  ok: boolean;
  script: ScriptDistribution;
  is_synced: boolean;
  line_count: number;
  preview_lines: string[];
  validation: LyricsValidation;
}> {
  const res = await fetch('/api/lyrics/validate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ raw_text, target_lang }),
  });
  if (!res.ok) throw new Error('Lyrics validation request failed');
  return res.json();
}

export async function prepareLyrics(raw_text: string, video_id?: string): Promise<{ ok: boolean; lyrics_file: string }> {
  const res = await fetch('/api/pipeline/prepare_lyrics', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ raw_text, video_id }),
  });
  if (!res.ok) throw new Error('Preparing lyrics temporary file failed');
  return res.json();
}

export async function saveProject(
  vid: string,
  lines: any[],
  customDictUpdates?: Record<string, Record<string, { pinyin: string; en: string }>>
) {
  const res = await fetch(`/api/project/${encodeURIComponent(vid)}/save`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      lines,
      custom_dict_updates: customDictUpdates,
    }),
  });
  if (!res.ok) throw new Error('Failed to save project');
  return res.json();
}

export async function updateProjectMeta(
  vid: string,
  meta: { title?: string; artist?: string }
): Promise<{ ok: boolean; metadata: any }> {
  const res = await fetch(`/api/project/${encodeURIComponent(vid)}/meta`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(meta),
  });
  if (!res.ok) throw new Error('Failed to update project metadata');
  return res.json();
}

export async function updateCustomDict(entries: Record<string, Record<string, { pinyin: string; en: string }>>) {
  const res = await fetch('/api/dict', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(entries),
  });
  if (!res.ok) throw new Error('Failed to update custom dictionary');
  return res.json();
}

export function subscribePipelineSSE(
  params: {
    url: string;
    query?: string;
    target_lang?: string;
    lyrics_file?: string;
    skip_demucs?: boolean;
    skip_netease?: boolean;
    force_lrc?: boolean;
    preview_only?: boolean;
  },
  onEvent: (data: any) => void,
  onError: (err: any) => void
): () => void {
  const q = new URLSearchParams();
  q.set('url', params.url);
  if (params.query) q.set('query', params.query);
  if (params.target_lang) q.set('target_lang', params.target_lang);
  if (params.lyrics_file) q.set('lyrics_file', params.lyrics_file);
  if (params.skip_demucs) q.set('skip_demucs', '1');
  if (params.skip_netease) q.set('skip_netease', '1');
  if (params.force_lrc) q.set('force_lrc', '1');
  if (params.preview_only) q.set('preview_only', '1');

  const es = new EventSource(`/api/pipeline/run?${q.toString()}`);
  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      onEvent(data);
      if (data.type === 'complete' || data.type === 'error') {
        es.close();
      }
    } catch (err) {
      onError(err);
    }
  };
  es.onerror = (e) => {
    onError(e);
    es.close();
  };
  return () => es.close();
}

export function subscribeRenderSSE(
  vid: string,
  previewSec: number | null,
  assOnly: boolean,
  onEvent: (data: any) => void,
  onError: (err: any) => void
): () => void {
  const q = new URLSearchParams();
  if (previewSec) q.set('preview', String(previewSec));
  if (assOnly) q.set('ass_only', '1');

  const es = new EventSource(`/api/project/${encodeURIComponent(vid)}/render?${q.toString()}`);
  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      onEvent(data);
      if (data.type === 'complete' || data.type === 'error') {
        es.close();
      }
    } catch (err) {
      onError(err);
    }
  };
  es.onerror = (e) => {
    onError(e);
    es.close();
  };
  return () => es.close();
}
