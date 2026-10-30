export interface Token {
  text: string;
  start: number;
  end: number;
  space?: boolean;
  is_deleted?: boolean;
  pinyin?: string;
  en?: string;
  comment?: string;
}

export interface Line {
  start: number;
  end: number;
  is_backing?: boolean;
  tokens: Token[];
}

export interface ProjectSummary {
  id: string;
  filename: string;
  title?: string;
  artist?: string;
  duration?: number;
  num_lines: number;
  has_audio: boolean;
  has_vocals: boolean;
  has_video: boolean;
}

export interface LintIssue {
  rule: string;
  detail: string;
}

export interface LintReportLine {
  line_index: number;
  start: number;
  end: number;
  is_backing: boolean;
  text: string;
  score: number;
  issues: LintIssue[];
}

export interface LintReport {
  json_path: string;
  vocals_path: string | null;
  total_lines: number;
  flagged_lines: number;
  total_issues: number;
  mean_confidence: number;
  lines: LintReportLine[];
}

export interface ProjectConfig {
  video: {
    width: number;
    height: number;
    fps: number;
    background_color: string;
  };
  subtitles: {
    font_name: string;
    ruby_columns?: boolean;
    position: {
      alignment: string;
      margin_bottom: number;
      line_spacing: number;
    };
    hanzi: {
      enabled: boolean;
      font_size: number;
      color_unfilled: string;
      color_filled: string;
      color_outline: string;
      outline_width: number;
      shadow: number;
      bold: boolean;
    };
    pinyin: {
      enabled: boolean;
      font_size: number;
      color_unfilled: string;
      color_filled: string;
      color_outline: string;
      outline_width: number;
      shadow: number;
      bold: boolean;
    };
    english: {
      enabled: boolean;
      font_size: number;
      color_unfilled: string;
      color_filled: string;
      color_outline: string;
      outline_width: number;
      shadow: number;
      bold: boolean;
    };
    backing: {
      placement: string;
      margin_right: number;
      margin_bottom: number;
      margin_left: number;
      font_scale: number;
      italic: boolean;
      color_filled: string;
      color_unfilled: string;
      color_outline: string;
      prefix: string;
      suffix: string;
    };
  };
}

export interface ProjectMetadata {
  video_id: string;
  title: string;
  artist: string;
  duration?: number;
  tier_mode?: string;
  bg_video_id?: string | null;
  bg_video_path?: string | null;
  bg_media_path?: string | null;
  bg_media_type?: 'video' | 'image' | null;
  bg_offset_ms?: number;
  bg_dim_pct?: number;
  bg_media_exists?: boolean;
  bg_stream_url?: string | null;
  asr_engine?: string;
  target_lang?: string;
}

export interface ProjectData {
  exists: boolean;
  video_id: string;
  metadata?: ProjectMetadata;
  lines: Line[];
  config: ProjectConfig;
  lint: LintReport | null;
  custom_dict: Record<string, Record<string, { pinyin: string; en: string }>>;
  has_vocals: boolean;
  has_mix: boolean;
  has_video: boolean;
}


export interface ScriptDistribution {
  total_chars: number;
  latin_ratio: number;
  han_ratio: number;
  hangul_ratio: number;
  kana_ratio: number;
  dominant: string;
}

export interface LyricsValidation {
  is_valid: boolean;
  reason: string;
  score: number;
}

export interface LyricsCandidate {
  id: string;
  source: string;
  title: string;
  artist: string;
  album: string;
  duration: number;
  is_synced: boolean;
  synced_lyrics: string;
  plain_lyrics: string;
  raw_lyrics: string;
  clean_lyrics?: string;
  line_count: number;
  script?: ScriptDistribution;
  validation: LyricsValidation;
}
