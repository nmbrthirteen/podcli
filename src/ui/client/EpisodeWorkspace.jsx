import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import {
  Check,
  Heart as HeartGlyph,
  MessageCircle,
  Bookmark as BookmarkGlyph,
  Forward,
  Music,
  User as UserGlyph,
  Search as SearchGlyph,
  Home as HomeGlyph,
  Users as UsersGlyph,
  Inbox,
  X,
  RotateCcw,
  Plus,
  Play,
  Pencil,
  Download,
  Download as DownloadGlyph,
  Activity,
  ChevronRight,
  ChevronDown,
  ArrowRight,
} from 'lucide-react';
import CopyButton from './CopyButton';
import AssetPicker from './AssetPicker';
import RecentSources from './RecentSources';
import { PageHeader } from './Page';

const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
const isHttpUrl = (value) => /^https?:\/\//i.test(value.trim());
    const api = async (path, opts = {}) => {
      const res = await fetch(`/api${path}`, { headers: { 'Content-Type': 'application/json', ...opts.headers }, ...opts });
      let body = null;
      try { body = await res.json(); } catch { /* empty / non-JSON body */ }
      if (!res.ok) return { error: (body && body.error) || `HTTP ${res.status}` };
      return body ?? {};
    };

    function uploadFile(file, onProgress) {
      return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        const fd = new FormData();
        fd.append('file', file);
        xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100)); };
        xhr.onload = () => { try { resolve(JSON.parse(xhr.responseText)); } catch (e) { reject(e); } };
        xhr.onerror = () => reject(new Error('Upload failed'));
        xhr.open('POST', '/api/upload');
        xhr.send(fd);
      });
    }

    function useJob(jobId) {
      const [state, setState] = useState(null);
      useEffect(() => {
        if (!jobId) { setState(null); return; }
        const es = new EventSource(`/api/job/${jobId}/stream`);
        es.onmessage = e => { const d = JSON.parse(e.data); setState(d); if (d.status === 'done' || d.status === 'error') es.close(); };
        es.onerror = () => es.close();
        return () => es.close();
      }, [jobId]);
      return state;
    }

    // --- MCP ↔ UI Bridge: SSE hook ---
    function useSSE() {
      const [lastEvent, setLastEvent] = useState(null);
      const [connected, setConnected] = useState(false);
      useEffect(() => {
        const es = new EventSource('/api/events');
        const handle = (type) => (e) => {
          try { setLastEvent({ type, data: JSON.parse(e.data), ts: Date.now() }); } catch { }
        };
        es.addEventListener('state', (e) => { setConnected(true); handle('state')(e); });
        es.addEventListener('state-sync', handle('state-sync'));
        es.addEventListener('export-started', handle('export-started'));
        es.addEventListener('job-update', handle('job-update'));
        es.addEventListener('job-complete', handle('job-complete'));
        es.addEventListener('job-error', handle('job-error'));
        es.onerror = () => { };
        return () => es.close();
      }, []);
      return { lastEvent, connected };
    }

    const CheckIcon = () => <Check size={12} color="#fff" strokeWidth={2.5} />;
    const CheckSmall = ({ color = 'var(--green)' }) => <Check size={10} color={color} strokeWidth={3} />;

    const PREVIEW_SCALE = 0.27;
    const px = (n) => Math.round(n * PREVIEW_SCALE);
    const PROD_TO_PCT = (margin) => ((margin / 1920) * 100).toFixed(1) + '%';

    const STYLE_CONFIGS = {
      branded: {
        fontSize: px(100), fontWeight: 700, bottom: PROD_TO_PCT(420),
        lineHeight: 1.2, uppercase: false,
        wordsPerChunk: 3, maxCharsPerChunk: 18, splitLines: true,
        color: '#fff', activeColor: '#fff',
        activePill: { background: 'rgba(0,0,0,0.85)', borderRadius: 8, paddingX: 8 },
        chunkBg: null, gradient: true,
        sample: ['Did', 'you', 'ever', 'felt', 'like', 'this'],
      },
      hormozi: {
        fontSize: px(90), fontWeight: 800, bottom: PROD_TO_PCT(400),
        lineHeight: 1.2, uppercase: true,
        wordsPerChunk: 3, maxCharsPerChunk: 22, splitLines: false,
        color: '#fff', activeColor: '#ffff00',
        activePill: null,
        chunkBg: { background: 'rgba(0,0,0,0.8)', borderRadius: 5, paddingX: 10, paddingY: 5 },
        gradient: false,
        sample: ['LOOKING', 'FOR', 'HIGH', 'ENERGY', 'CONTENT'],
      },
      karaoke: {
        fontSize: px(80), fontWeight: 600, bottom: PROD_TO_PCT(400),
        lineHeight: 1.25, uppercase: false,
        wordsPerChunk: 5, maxCharsPerChunk: 28, splitLines: false,
        color: 'rgba(255,255,255,0.4)', activeColor: '#fff',
        activePill: null, chunkBg: null, gradient: false,
        sample: ['the', 'secret', 'to', 'building', 'something'],
      },
      subtle: {
        fontSize: px(64), fontWeight: 400, bottom: PROD_TO_PCT(200),
        lineHeight: 1.3, uppercase: false,
        wordsPerChunk: 6, maxCharsPerChunk: 36, splitLines: false,
        color: 'rgba(255,255,255,0.95)', activeColor: 'rgba(255,255,255,0.95)',
        activePill: null, chunkBg: null, gradient: false,
        sample: ['the', 'secret', 'to', 'building', 'something', 'people'],
      },
    };

    function buildPreviewChunks(words, perChunk, maxChars) {
      const out = [];
      let i = 0;
      while (i < words.length) {
        let end = i, count = 0;
        while (end < words.length && end - i < perChunk) {
          const w = words[end];
          const next = count === 0 ? w.length : count + 1 + w.length;
          if (end > i && maxChars && next > maxChars) break;
          count = next; end++;
        }
        if (end === i) end = i + 1;
        if (words.length - end === 1 && end - i > 2) end -= 1;
        out.push(words.slice(i, end));
        i = end;
      }
      return out;
    }
    function splitBrandedLines(chunk) {
      if (chunk.length <= 2) return [chunk, []];
      return [chunk.slice(0, 2), chunk.slice(2)];
    }


    function TikTokWireframe({ activeClip, captionStyle }) {
      const Heart = () => <HeartGlyph size={28} fill="#fff" strokeWidth={0} />;
      const Comment = () => <MessageCircle size={28} fill="#fff" strokeWidth={0} />;
      const Bookmark = () => <BookmarkGlyph size={26} fill="#fff" strokeWidth={0} />;
      const Share = () => <Forward size={28} color="#fff" fill="#fff" strokeWidth={1.5} />;
      const MusicNote = () => <Music size={11} color="#fff" strokeWidth={2.5} />;
      const Person = () => <UserGlyph fill="#fff" strokeWidth={0} style={{ width: '100%', height: '100%' }} />;
      const Search = () => <SearchGlyph color="#fff" strokeWidth={2.2} style={{ width: '100%', height: '100%' }} />;
      const NavHome = () => <HomeGlyph className="tt-nav-icon" fill="#fff" color="#fff" strokeWidth={1.5} />;
      const NavFriends = () => <UsersGlyph className="tt-nav-icon" fill="#fff" color="#fff" strokeWidth={1.5} />;
      const NavInbox = () => <Inbox className="tt-nav-icon" color="#fff" strokeWidth={2.2} />;
      const NavProfile = () => <UserGlyph className="tt-nav-icon" fill="#fff" color="#fff" strokeWidth={1.5} />;
      const fmtCount = (n) => {
        if (n >= 100000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
        if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
        return String(n);
      };
      const likes = fmtCount(999000);
      const comments = fmtCount(2463);
      const saves = fmtCount(60500);
      const shares = fmtCount(2230);
      const caption = activeClip ? activeClip.title : 'Description #shorts #podcast';
      return (
        <div className="tt-overlay">
          <div className="tt-top">
            <span className="tt-tab">
              Following
              <span className="tt-following-dot" />
            </span>
            <span className="tt-divider" />
            <span className="tt-tab active">For You</span>
          </div>
          <div className="tt-search"><Search /></div>
          <div className="tt-rail">
            <div className="tt-avatar-wrap">
              <div className="tt-avatar"><Person /></div>
              <div className="tt-follow">+</div>
            </div>
            <div className="tt-action"><div className="tt-icon"><Heart /></div><span>{likes}</span></div>
            <div className="tt-action"><div className="tt-icon"><Comment /></div><span>{comments}</span></div>
            <div className="tt-action"><div className="tt-icon"><Bookmark /></div><span>{saves}</span></div>
            <div className="tt-action"><div className="tt-icon"><Share /></div><span>{shares}</span></div>
            <div className="tt-disc" />
          </div>
          <div className="tt-bottom">
            <div className="tt-handle">USERNAME</div>
            <div className="tt-desc">{caption}<span className="tt-more"> more</span></div>
            <div className="tt-translate">see translation</div>
            <div className="tt-music">
              <span className="tt-music-note"><MusicNote /></span>
              <span>Original Sound</span>
            </div>
          </div>
          <div className="tt-nav">
            <div className="tt-nav-item active"><NavHome /><span>Home</span></div>
            <div className="tt-nav-item"><NavFriends /><span>Friends</span></div>
            <div className="tt-nav-item"><div className="tt-plus">+</div></div>
            <div className="tt-nav-item"><NavInbox /><span>Inbox</span></div>
            <div className="tt-nav-item"><NavProfile /><span>Profile</span></div>
          </div>
        </div>
      );
    }

    function PhoneCaptionBody({ chunk, activeWordInChunk, cfg }) {
      if (!chunk || !chunk.length) return null;
      const fmt = (w) => (cfg.uppercase ? w.toUpperCase() : w);

      const renderWord = (w, i, isActive) => {
        if (cfg.activePill && isActive) {
          // Branded: black rounded pill behind the active word.
          return (
            <span key={`${w}-${i}`} style={{ position: 'relative', display: 'inline-block' }}>
              <span style={{
                position: 'absolute',
                top: -2, bottom: -2,
                left: -cfg.activePill.paddingX, right: -cfg.activePill.paddingX,
                background: cfg.activePill.background,
                borderRadius: cfg.activePill.borderRadius,
                boxShadow: '0 2px 8px rgba(0,0,0,0.45)',
                pointerEvents: 'none',
              }} />
              <span style={{ position: 'relative', color: cfg.activeColor, zIndex: 1 }}>{fmt(w)}</span>
            </span>
          );
        }
        return (
          <span key={`${w}-${i}`} style={{
            color: isActive ? cfg.activeColor : cfg.color,
            textShadow: isActive && cfg.activeColor === '#ffff00'
              ? '0 0 12px rgba(255,255,0,0.5)' : '0 1px 4px rgba(0,0,0,0.6)',
            transition: 'color 0.15s var(--ease)',
          }}>{fmt(w)}</span>
        );
      };

      // Branded: split chunk into [first 2 words, rest], render as 2 lines.
      if (cfg.splitLines) {
        const [line1, line2] = splitBrandedLines(chunk);
        const startIdx2 = line1.length;
        return (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            <div style={{ whiteSpace: 'nowrap' }}>
              {line1.map((w, i) => (
                <React.Fragment key={`l1-${i}`}>
                  {i > 0 && ' '}
                  {renderWord(w, i, i === activeWordInChunk)}
                </React.Fragment>
              ))}
            </div>
            {line2.length > 0 && (
              <div style={{ whiteSpace: 'nowrap' }}>
                {line2.map((w, i) => (
                  <React.Fragment key={`l2-${i}`}>
                    {i > 0 && ' '}
                    {renderWord(w, startIdx2 + i, startIdx2 + i === activeWordInChunk)}
                  </React.Fragment>
                ))}
              </div>
            )}
          </div>
        );
      }

      // Hormozi: whole chunk inside a single black box, active word yellow.
      // Karaoke / subtle: no box, just inline words.
      const inner = chunk.map((w, i) => (
        <React.Fragment key={i}>
          {i > 0 && ' '}
          {renderWord(w, i, i === activeWordInChunk)}
        </React.Fragment>
      ));

      if (cfg.chunkBg) {
        return (
          <div style={{
            display: 'inline-block',
            background: cfg.chunkBg.background,
            borderRadius: cfg.chunkBg.borderRadius,
            padding: `${cfg.chunkBg.paddingY}px ${cfg.chunkBg.paddingX}px`,
            whiteSpace: 'nowrap',
          }}>{inner}</div>
        );
      }
      return <div style={{ whiteSpace: 'normal' }}>{inner}</div>;
    }

    function LivePhonePreview({ videoUrl, videoRef, captionStyle, activeClip, transcriptWords, logoPath, previewSrc, showTikTokFrame, onToggleFrame }) {
      const cfg = STYLE_CONFIGS[captionStyle] || STYLE_CONFIGS.branded;
      const [logoBroken, setLogoBroken] = useState(false);
      useEffect(() => { setLogoBroken(false); }, [logoPath]);

      // Pull all transcribed words from the active clip (or first N from the
      // whole transcript). The chunker decides how many show at once.
      const sourcePool = useMemo(() => {
        if (!transcriptWords || !transcriptWords.length) return null;
        let pool = transcriptWords;
        if (activeClip) {
          pool = transcriptWords.filter(w =>
            w.end > activeClip.start_second && w.start < activeClip.end_second
          );
        }
        if (!pool.length) return null;
        const out = pool.slice(0, 80).map(w => (w.word || w.text || '').trim()).filter(Boolean);
        return out.length >= 2 ? out : null;
      }, [activeClip, transcriptWords]);

      const usingSample = !sourcePool;
      const poolWords = sourcePool || cfg.sample;

      // Chunk the same way the Remotion renderer does. The active chunk
      // is what appears on screen; the active word inside it gets the
      // pill / accent color.
      const chunks = useMemo(
        () => buildPreviewChunks(poolWords, cfg.wordsPerChunk, cfg.maxCharsPerChunk),
        [poolWords, cfg.wordsPerChunk, cfg.maxCharsPerChunk]
      );

      const [cursor, setCursor] = useState(0); // global word index
      useEffect(() => {
        setCursor(0);
        if (poolWords.length <= 1) return;
        const tick = captionStyle === 'karaoke' ? 320 : 420;
        const iv = setInterval(() => setCursor(i => (i + 1) % poolWords.length), tick);
        return () => clearInterval(iv);
      }, [captionStyle, poolWords.length, poolWords[0]]);

      // Find which chunk the cursor is currently in, and which word within it.
      let activeChunkIdx = 0, activeWordInChunk = 0, consumed = 0;
      for (let ci = 0; ci < chunks.length; ci++) {
        const len = chunks[ci].length;
        if (cursor < consumed + len) {
          activeChunkIdx = ci;
          activeWordInChunk = cursor - consumed;
          break;
        }
        consumed += len;
      }
      const activeChunk = chunks[activeChunkIdx] || [];

      return (
       <>
        <div className="phone-frame fade-in">
          <div className="phone-notch" />
          {videoUrl ? (
            <video key={videoUrl} ref={videoRef} src={videoUrl}
              muted playsInline preload="auto"
              className={previewSrc ? '' : ''} />
          ) : (
            <div className="phone-empty">
              <div style={{ opacity: 0.4, display: 'flex' }}><Play size={22} /></div>
              <div>Drop a video to see live caption preview</div>
            </div>
          )}
          {videoUrl && cfg.gradient && <div className="phone-gradient" />}
          {videoUrl && captionStyle === 'branded' && logoPath && !logoBroken && (
            <div className="phone-logo">
              <img src={`/api/stream-source?path=${encodeURIComponent(logoPath)}`}
                onError={() => setLogoBroken(true)}
                style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
            </div>
          )}
          {videoUrl && (
            <div className="phone-caption" style={{
              bottom: cfg.bottom,
              fontSize: cfg.fontSize, fontWeight: cfg.fontWeight,
              lineHeight: cfg.lineHeight,
              fontFamily: "'DM Sans', 'Inter', Arial, sans-serif",
            }}>
              <PhoneCaptionBody
                chunk={activeChunk}
                activeWordInChunk={activeWordInChunk}
                cfg={cfg}
              />
              {usingSample && (
                <div style={{
                  marginTop: 6, fontSize: 9, fontWeight: 600, color: 'rgba(255,255,255,0.55)',
                  textShadow: '0 1px 3px rgba(0,0,0,0.6)', letterSpacing: '0.5px',
                  textTransform: 'uppercase', pointerEvents: 'none',
                }}>
                  Placeholder · transcribe to see your captions
                </div>
              )}
            </div>
          )}
          {videoUrl && showTikTokFrame && (
            <TikTokWireframe activeClip={activeClip} captionStyle={captionStyle} />
          )}
        </div>
        <div className="preview-toggle-row">
          <label>
            <input type="checkbox" checked={!!showTikTokFrame} onChange={onToggleFrame} />
            TikTok wireframe
          </label>
        </div>
       </>
      );
    }

    /* ── Spec Recap ── re-renders form state as a reviewable card. */
    function SpecRecap({ captionStyle, cropStrategy, logoPath, outroPath, activePreset, quality, cleanFillers }) {
      const cfg = STYLE_CONFIGS[captionStyle] || STYLE_CONFIGS.branded;
      // Sample "color" comes from the active-word style of the caption preset.
      const swatch = (cfg.activeStyle && (cfg.activeStyle.color || cfg.activeStyle.background)) || '#ffffff';
      const rows = [
        ['Caption style', captionStyle],
        ['Crop', cropStrategy],
        ['Font size', `${cfg.fontSize}px`],
        ['Highlight', <span><span className="sr-swatch" style={{ background: swatch }} />{swatch}</span>],
        ['Quality', quality || 'standard'],
        ['Clean fillers', cleanFillers ? 'on' : 'off'],
      ];
      if (logoPath) rows.push(['Logo', logoPath.split('/').pop()]);
      if (outroPath) rows.push(['Outro', outroPath.split('/').pop()]);
      if (activePreset) rows.push(['Preset', activePreset]);
      return (
        <div className="spec-recap fade-in">
          {rows.map(([k, v]) => (
            <div className="sr-row" key={k}>
              <span className="sr-key">{k}</span>
              <span className="sr-val">{v}</span>
            </div>
          ))}
        </div>
      );
    }

    /* ── MCP Hints Component ── */
    function McpHints({ phase, videoPath, transcript, transcriptText, suggestions, mcpConnected }) {
      const [hints, setHints] = useState([]);
      const [copied, setCopied] = useState(null);
      const [collapsed, setCollapsed] = useState(false);

      // Fetch hints whenever state changes
      useEffect(() => {
        if (!mcpConnected) return;
        fetch('/api/mcp-hints').then(r => r.json()).then(d => {
          if (d.hints) setHints(d.hints);
        }).catch(() => { });
      }, [phase, videoPath, !!transcript, !!transcriptText, suggestions?.length, mcpConnected]);

      const markCopied = (idx) => {
          setCopied(idx);
          setTimeout(() => setCopied(null), 1500);
      };

      const copyPrompt = (prompt, idx) => {
        navigator.clipboard.writeText(prompt).then(() => {
          markCopied(idx);
        }).catch(() => { });
      };

      // Only show when MCP is connected AND there are actionable hints
      if (!mcpConnected || hints.length === 0) return null;

      return (
        <div className="mcp-hints">
          <div className="mcp-hints-header" style={{ cursor: 'pointer' }} onClick={() => setCollapsed(!collapsed)}>
            <div className="mcp-hints-icon">AI</div>
            <div className="mcp-hints-title">MCP prompts</div>
            <div className="mcp-hints-subtitle">
              {collapsed ? `${hints.length} prompts` : 'Click to copy'}
            </div>
            <span className="hint-xs" style={{ transition: 'transform 0.2s', transform: collapsed ? 'rotate(-90deg)' : 'rotate(0)', marginLeft: 4 }}><ChevronDown size={12} /></span>
          </div>
          {!collapsed && (
            <div className="mcp-hint-list">
              {hints.slice(0, 5).map((hint, i) => (
                <div key={i}
                  className={`mcp-hint ${copied === i ? 'copied' : ''}`}
                  onClick={() => copyPrompt(hint.prompt, i)}>
                  <span className={`mcp-hint-category ${hint.category}`}>{hint.category}</span>
                  <span className="mcp-hint-prompt">
                    {copied === i ? 'Copied!' : hint.prompt}
                  </span>
                  <span className="mcp-hint-desc">{hint.description}</span>
                  <CopyButton
                    className="mcp-hint-copy"
                    text={hint.prompt}
                    title="Copy prompt"
                    iconOnly
                    stopPropagation
                    onCopied={() => markCopied(i)}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      );
    }

    export default function App() {
      const [videoPath, setVideoPath] = useState('');
      const [transcriptMode, setTranscriptMode] = useState('whisper');
      const [transcriptText, setTranscriptText] = useState('');
      const [timeAdjust, setTimeAdjust] = useState(-1);
      const [transcriptionEngine, setTranscriptionEngine] = useState('whisper');
      const [assemblyAiKey, setAssemblyAiKey] = useState('');
      const [whisperModel, setWhisperModel] = useState('base');
      const [captionStyle, setCaptionStyle] = useState('branded');
      const [cropStrategy, setCropStrategy] = useState('face');
      const [format, setFormat] = useState('vertical');
      const [showTikTokFrame, setShowTikTokFrame] = useState(false);
      const [logoPath, setLogoPath] = useState('');
      const [outroPath, setOutroPath] = useState('');
      const [introPath, setIntroPath] = useState('');
      const initializedRef = useRef(false);
      const videoFileRef = useRef();
      const [transcriptDragOver, setTranscriptDragOver] = useState(false);
      const [transcriptFileName, setTranscriptFileName] = useState('');

      const [phase, setPhase] = useState('idle');
      const [file, setFile] = useState(null);
      const [transcript, setTranscript] = useState(null);
      const [suggestions, setSuggestions] = useState([]);
      const [deselected, setDeselected] = useState(new Set());
      const [batchJobId, setBatchJobId] = useState(null);
      const batchStream = useJob(batchJobId);
      const [results, setResults] = useState([]);
      const [error, setError] = useState(null);
      const [previewFile, setPreviewFile] = useState(null);
      const [momentText, setMomentText] = useState('');
      const [findingMoment, setFindingMoment] = useState(false);
      const [momentNotice, setMomentNotice] = useState(null);

      const [retryIdx, setRetryIdx] = useState(null);
      const [retryJobId, setRetryJobId] = useState(null);
      const retryStream = useJob(retryJobId);
      const [browsing, setBrowsing] = useState(false);
      const [downloadingVideo, setDownloadingVideo] = useState(false);
      const [downloadJobId, setDownloadJobId] = useState(null);
      const downloadStream = useJob(downloadJobId);
      const [clipHistory, setClipHistory] = useState([]);
      const [historyOpen, setHistoryOpen] = useState(false);

      const [encoderInfo, setEncoderInfo] = useState(null);
      const [speakerStatus, setSpeakerStatus] = useState(null);
      const [generateCopied, setGenerateCopied] = useState(false);
      const [corrections, setCorrections] = useState({});
      const [correctionWord, setCorrectionWord] = useState('');
      const [correctionFix, setCorrectionFix] = useState('');
      const [correctionsOpen, setCorrectionsOpen] = useState(false);

      // Presets
      const [presets, setPresets] = useState([]);
      const [activePreset, setActivePreset] = useState('');
      const [showPresetSave, setShowPresetSave] = useState(false);
      const [presetName, setPresetName] = useState('');
      const [presetSaving, setPresetSaving] = useState(false);

      // Advanced settings
      const [advancedOpen, setAdvancedOpen] = useState(false);
      const [cleanFillers, setCleanFillers] = useState(true);
      const [quality, setQuality] = useState('max');
      const [topClips, setTopClips] = useState(8);
      const [minDuration, setMinDuration] = useState(20);
      const [maxDuration, setMaxDuration] = useState(45);
      const [energyBoost, setEnergyBoost] = useState(true);

      // Clip editing
      const [editingClip, setEditingClip] = useState(null); // index
      const [editForm, setEditForm] = useState({ title: '', start: 0, end: 0 });

      // Energy analysis
      const [energyData, setEnergyData] = useState({}); // clip_id → { peak, avg, level }
      const [analyzingEnergy, setAnalyzingEnergy] = useState(false);

      // Auto-transcribe + cache
      const [transcribing, setTranscribing] = useState(false);
      const [transcribeJobId, setTranscribeJobId] = useState(null);
      const transcribeStream = useJob(transcribeJobId);
      const [cachedTranscript, setCachedTranscript] = useState(false);
      useEffect(() => {
        api('/encoder-info').then(d => { if (d.best) setEncoderInfo(d); }).catch(() => { });
        api('/speaker-status').then(d => setSpeakerStatus(d)).catch(() => { });
        api('/corrections').then(d => { if (d && typeof d === 'object') setCorrections(d); }).catch(() => { });
      }, []);

      // Fetch presets on mount
      const fetchPresets = () => { api('/presets').then(d => { if (d.presets) setPresets(d.presets); }).catch(() => {}); };
      useEffect(fetchPresets, []);

      const loadPreset = async (name) => {
        if (!name) { setActivePreset(''); return; }
        try {
          const response = await api('/presets', { method: 'POST', body: JSON.stringify({ action: 'get', name }) });
          const d = response.config || response;
          if (d.caption_style) setCaptionStyle(d.caption_style);
          if (d.crop_strategy) setCropStrategy(d.crop_strategy);
          if (d.format) setFormat(d.format);
          if (d.logo_path !== undefined) setLogoPath(d.logo_path || '');
          if (d.outro_path !== undefined) setOutroPath(d.outro_path || '');
          if (d.intro_path !== undefined) setIntroPath(d.intro_path || '');
          if (d.video_path !== undefined) {
            const nextVideoPath = d.video_path || '';
            const changedVideo = nextVideoPath.trim() !== videoPath.trim();
            setVideoPath(nextVideoPath);
            setFile(null);
            if (changedVideo) {
              setTranscript(null);
              setCachedTranscript(false);
              setTranscriptText('');
              setTranscriptFileName('');
              setSuggestions([]);
              setDeselected(new Set());
              setResults([]);
              setEnergyData({});
              setPreviewSrc(null);
              setActiveClipIdx(null);
              autoTranscribeRef.current = '';
              setPhase('idle');
            }
          }
          if (d.clean_fillers !== undefined) setCleanFillers(d.clean_fillers);
          if (d.quality) setQuality(d.quality);
          if (d.top_clips) setTopClips(d.top_clips);
          if (d.min_clip_duration) setMinDuration(d.min_clip_duration);
          if (d.max_clip_duration) setMaxDuration(d.max_clip_duration);
          if (d.energy_boost !== undefined) setEnergyBoost(d.energy_boost);
          if (d.whisper_model) setWhisperModel(d.whisper_model);
          if (d.transcription_engine) setTranscriptionEngine(d.transcription_engine);
          if (d.time_adjust !== undefined) setTimeAdjust(d.time_adjust);
          setActivePreset(name);
        } catch {}
      };

      const savePreset = async () => {
        if (!presetName.trim()) return;
        setPresetSaving(true);
        try {
          await api('/presets', { method: 'POST', body: JSON.stringify({
            action: 'save', name: presetName.trim(),
            config: { caption_style: captionStyle, crop_strategy: cropStrategy, format, logo_path: logoPath, outro_path: outroPath, intro_path: introPath, video_path: videoPath.trim(), whisper_model: whisperModel, transcription_engine: transcriptionEngine, time_adjust: timeAdjust, clean_fillers: cleanFillers, quality, top_clips: topClips, min_clip_duration: minDuration, max_clip_duration: maxDuration, energy_boost: energyBoost }
          })});
          setActivePreset(presetName.trim());
          setPresetName(''); setShowPresetSave(false);
          fetchPresets();
        } catch {}
        finally { setPresetSaving(false); }
      };

      const deletePreset = async (name) => {
        if (!confirm(`Delete preset "${name}"?`)) return;
        try {
          await api('/presets', { method: 'POST', body: JSON.stringify({ action: 'delete', name }) });
          if (activePreset === name) setActivePreset('');
          fetchPresets();
        } catch {}
      };

      // Clip editing
      const openClipEdit = (idx, e) => {
        e.stopPropagation();
        const clip = suggestions[idx];
        setEditingClip(idx);
        setEditForm({ title: clip.title, start: clip.start_second, end: clip.end_second });
      };

      const saveClipEdit = () => {
        if (editingClip === null) return;
        setSuggestions(prev => {
          const next = [...prev];
          next[editingClip] = { ...next[editingClip], title: editForm.title, start_second: editForm.start, end_second: editForm.end, duration: Math.round(editForm.end - editForm.start) };
          return next;
        });
        setEditingClip(null);
      };

      const deleteClipEdit = () => {
        if (editingClip === null) return;
        if (!confirm(`Delete clip "${editForm.title}" from this batch?`)) return;
        setSuggestions(prev => prev.filter((_, i) => i !== editingClip));
        setDeselected(prev => {
          const next = new Set();
          for (const idx of prev) {
            if (idx < editingClip) next.add(idx);
            else if (idx > editingClip) next.add(idx - 1);
          }
          return next;
        });
        setEditingClip(null);
      };

      // Energy analysis
      const analyzeEnergy = async () => {
        setAnalyzingEnergy(true);
        try {
          const segments = suggestions.map(c => ({ start: c.start_second, end: c.end_second }));
          const vp = file?.file_path || videoPath.trim();
          const d = await api('/analyze-energy', { method: 'POST', body: JSON.stringify({ video_path: vp, segments }) });
          // Backend returns { segment_scores: [0-10, ...], peak_times: [...] }
          if (d.segment_scores && Array.isArray(d.segment_scores)) {
            const map = {};
            d.segment_scores.forEach((score, i) => {
              if (suggestions[i]) {
                const level = score >= 7 ? 'high' : score >= 4 ? 'medium' : 'low';
                map[i] = { score: score, level };
              }
            });
            setEnergyData(map);
          }
        } catch {}
        finally { setAnalyzingEnergy(false); }
      };

      // Auto-transcribe when video is set and in whisper mode with no transcript
      const autoTranscribe = async (vp) => {
        if (!vp || transcribing || transcript) return;
        setTranscribing(true);
        setCachedTranscript(false);
        try {
          const fileData = await api('/select-file', { method: 'POST', body: JSON.stringify({ file_path: vp }) });
          if (fileData.error) { setTranscribing(false); return; }
          setFile(fileData);
          const engine = transcriptionEngine === 'assemblyai' ? 'assemblyai' : undefined;
          const data = await api('/transcribe', { method: 'POST', body: JSON.stringify({
            file_path: vp,
            model_size: whisperModel,
            engine,
            assemblyai_api_key: transcriptionEngine === 'assemblyai' ? assemblyAiKey.trim() : undefined,
            enable_diarization: transcriptionEngine === 'assemblyai' || (speakerStatus?.configured || false),
          }) });
          if (data.error) {
            setError(data.error);
            setTranscribing(false);
            return;
          }
          if (data.cached && data.data) {
            // Instant cache hit
            setTranscript(data.data);
            setCachedTranscript(true);
            setTranscribing(false);
          } else if (data.job_id) {
            setTranscribeJobId(data.job_id);
          } else {
            setTranscribing(false);
          }
        } catch { setTranscribing(false); }
      };

      // Watch transcribe job completion
      useEffect(() => {
        if (!transcribeStream) return;
        if (transcribeStream.status === 'done') {
          setTranscript(transcribeStream.result);
          setTranscribing(false);
          setTranscribeJobId(null);
        } else if (transcribeStream.status === 'error') {
          setError('Transcription failed: ' + (transcribeStream.error || 'Unknown error'));
          setTranscribing(false);
          setTranscribeJobId(null);
        }
      }, [transcribeStream?.status]);

      // Auto-trigger transcribe when video path is set and auto transcript mode is active
      const autoTranscribeRef = useRef('');
      useEffect(() => {
        const vp = videoPath.trim();
        if (transcriptMode === 'whisper' && transcriptionEngine === 'whisper' && vp && !isHttpUrl(vp) && !transcript && !transcribing && vp !== autoTranscribeRef.current) {
          autoTranscribeRef.current = vp;
          autoTranscribe(vp);
        }
      }, [videoPath, transcriptMode, transcriptionEngine, transcript, transcribing]);

      // Fetch clip history on mount and after exports
      const fetchHistory = () => { fetch('/api/history?limit=50').then(r => r.json()).then(d => { if (Array.isArray(d)) setClipHistory(d); }).catch(() => { }); };
      useEffect(fetchHistory, []);
      useEffect(() => { if (phase === 'done') fetchHistory(); }, [phase]);

      // --- MCP ↔ UI Bridge: connect SSE + sync state ---
      const { lastEvent: sseEvent, connected: mcpConnected } = useSSE();

      // Sync UI state to server on changes (fire-and-forget)
      // Guard: don't sync until initial SSE state has been received to avoid overwriting persisted state with defaults
      const prevSyncRef = useRef('');
      useEffect(() => {
        if (!initializedRef.current) return;
        const state = {
          _source: 'ui',
          videoPath,
          filePath: file?.file_path || '',
          suggestions,
          deselectedIndices: Array.from(deselected),
          settings: { captionStyle, cropStrategy, format, logoPath, outroPath, introPath },
          phase,
          results,
          energyData,
        };
        const key = JSON.stringify(state);
        if (key === prevSyncRef.current) return;
        prevSyncRef.current = key;
        fetch('/api/ui-state', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: key }).catch(() => { });
      }, [videoPath, file, suggestions, deselected, captionStyle, cropStrategy, format, logoPath, outroPath, introPath, phase, results, energyData]);

      // Sync transcript separately (large payload)
      const prevTranscriptRef = useRef(null);
      useEffect(() => {
        if (!initializedRef.current) return;
        if (!transcript || transcript === prevTranscriptRef.current) return;
        prevTranscriptRef.current = transcript;
        fetch('/api/ui-state', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ _source: 'ui', transcript }) }).catch(() => { });
      }, [transcript]);

      // Sync raw transcript text so MCP can read it before pipeline runs
      const prevRawRef = useRef('');
      useEffect(() => {
        if (!initializedRef.current) return;
        if (transcriptText === prevRawRef.current) return;
        prevRawRef.current = transcriptText;
        if (transcriptText.trim()) {
          fetch('/api/ui-state', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ _source: 'ui', rawTranscriptText: transcriptText }) }).catch(() => { });
        }
      }, [transcriptText]);

      // React to SSE events from MCP
      const lastHandledTsRef = useRef(0);
      useEffect(() => {
        if (!sseEvent || sseEvent.ts === lastHandledTsRef.current) return;
        lastHandledTsRef.current = sseEvent.ts;

        if (sseEvent.type === 'state-sync' || sseEvent.type === 'state') {
          const d = sseEvent.data;
          if (d.suggestions) {
            setSuggestions(d.suggestions);
            setEnergyData(sseEvent.type === 'state' && d.energyData ? d.energyData : {});
          }
          if (d.deselectedIndices !== undefined) setDeselected(new Set(d.deselectedIndices));
          else if (d.suggestions && !d.deselectedIndices) setDeselected(new Set());
          if (d.phase) setPhase(d.phase);
          if (sseEvent.type === 'state' && Array.isArray(d.results)) setResults(d.results);
          if (d.activeExportJobId !== undefined) setBatchJobId(d.activeExportJobId);
          if (d.videoPath !== undefined) setVideoPath(d.videoPath);
          if (d.transcript !== undefined) {
            setTranscript(d.transcript);
            if (d.videoPath) autoTranscribeRef.current = d.videoPath;
            if (d.transcript === null) autoTranscribeRef.current = '';
          }
          if (d.rawTranscriptText !== undefined && (d.transcript === null || !transcript)) setTranscriptText(d.rawTranscriptText);
          if (d.settings) {
            if (d.settings.captionStyle) setCaptionStyle(d.settings.captionStyle);
            if (d.settings.cropStrategy) setCropStrategy(d.settings.cropStrategy);
            if (d.settings.format) setFormat(d.settings.format);
            if (d.settings.logoPath !== undefined) setLogoPath(d.settings.logoPath);
            if (d.settings.outroPath !== undefined) setOutroPath(d.settings.outroPath);
            if (d.settings.introPath !== undefined) setIntroPath(d.settings.introPath);
          }
          // Mark initialized after first state restoration so sync useEffects don't overwrite with defaults
          if (sseEvent.type === 'state') {
            initializedRef.current = true;
          }
        } else if (sseEvent.type === 'export-started') {
          setBatchJobId(sseEvent.data.jobId);
          setPhase('exporting');
        } else if (sseEvent.type === 'job-complete') {
          if (phase === 'exporting' || sseEvent.data.result?.results) {
            setPhase('done');
            setResults(sseEvent.data.result?.results || []);
            setBatchJobId(null);
          }
        } else if (sseEvent.type === 'job-error') {
          if (phase === 'exporting') {
            setError('Export failed: ' + (sseEvent.data.error || 'Unknown error'));
            setPhase('review');
            setBatchJobId(null);
          }
        }
        // phase/transcript are read above; the ts-guard makes re-runs on their
        // change a no-op, so depending on them just keeps the reads fresh.
      }, [sseEvent, phase, transcript]);

      // Preview panel state
      const videoRef = useRef();
      const [activeClipIdx, setActiveClipIdx] = useState(null);
      const [previewSrc, setPreviewSrc] = useState(null); // null=source, string=rendered clip filename
      const [settingsFlash, setSettingsFlash] = useState(null);

      // Video source URL
      const videoUrl = previewSrc
        ? `/api/preview/${previewSrc}`
        : videoPath && !isHttpUrl(videoPath)
          ? `/api/stream-source?path=${encodeURIComponent(videoPath)}`
          : null;

      // Seek to clip when active clip changes (and showing source)
      useEffect(() => {
        if (activeClipIdx === null || previewSrc) return;
        const clip = suggestions[activeClipIdx];
        if (!clip || !videoRef.current) return;
        const seek = () => {
          videoRef.current.currentTime = clip.start_second;
          videoRef.current.play().catch(() => { });
        };
        if (videoRef.current.readyState >= 1) seek();
        else videoRef.current.addEventListener('loadedmetadata', seek, { once: true });
      }, [activeClipIdx, previewSrc]);

      // Flash settings when they change
      const flashSetting = (key) => {
        setSettingsFlash(key);
        setTimeout(() => setSettingsFlash(null), 600);
      };

      const onCaptionChange = (v) => { setCaptionStyle(v); flashSetting('caption'); };
      const onCropChange = (v) => { setCropStrategy(v); flashSetting('crop'); };
      const onFormatChange = (v) => { setFormat(v); flashSetting('format'); };

      // Click clip row → seek source video
      const onClipClick = (idx) => {
        setActiveClipIdx(idx);
        if (previewSrc) setPreviewSrc(null);
      };

      // Play rendered clip in preview panel
      const onPlayRendered = (filename) => {
        setPreviewSrc(filename);
        setActiveClipIdx(null);
      };

      const setUploadedVideo = useCallback(async (file) => {
        if (!file) return;
        setBrowsing(true); setError(null);
        try {
          const d = await uploadFile(file, () => { });
          if (d.error) setError(d.error);
          if (d.file_path) setVideoPath(d.file_path);
        } catch (e) { setError('Upload failed: ' + e.message); }
        finally { setBrowsing(false); }
      }, []);

      const doBrowse = useCallback(() => {
        videoFileRef.current?.click();
      }, []);

      const handleVideoFileSelect = (e) => {
        const f = e.target.files?.[0];
        e.target.value = '';
        setUploadedVideo(f);
      };

      const downloadVideo = async () => {
        const url = videoPath.trim();
        if (!url || downloadingVideo) return;
        setDownloadingVideo(true); setError(null);
        try {
          const d = await api('/download-video', { method: 'POST', body: JSON.stringify({ url }) });
          if (d.error) { setError(d.error); setDownloadingVideo(false); return; }
          if (d.job_id) { setDownloadJobId(d.job_id); return; }
          setError('Download failed: missing job id');
          setDownloadingVideo(false);
        } catch (e) { setError('Download failed: ' + e.message); setDownloadingVideo(false); }
      };

      useEffect(() => {
        if (!downloadStream) return;
        if (downloadStream.status === 'done') {
          const d = downloadStream.result;
          if (!d?.file_path) {
            setError('Download finished without a video file.');
            setDownloadingVideo(false);
            setDownloadJobId(null);
            return;
          }
          setFile(d);
          setVideoPath(d.file_path);
          setTranscript(null);
          setCachedTranscript(false);
          setTranscriptText('');
          setTranscriptFileName('');
          setSuggestions([]);
          setDeselected(new Set());
          setResults([]);
          setEnergyData({});
          setPreviewSrc(null);
          setActiveClipIdx(null);
          autoTranscribeRef.current = '';
          setDownloadingVideo(false);
          setDownloadJobId(null);
        } else if (downloadStream.status === 'error') {
          setError('Download failed: ' + (downloadStream.error || 'Unknown error'));
          setDownloadingVideo(false);
          setDownloadJobId(null);
        }
      }, [downloadStream?.status]);

      const findMoment = async () => {
        const text = momentText.trim();
        if (!text || findingMoment) return;
        setFindingMoment(true); setError(null); setMomentNotice(null);
        try {
          const d = await api('/find-moment', { method: 'POST', body: JSON.stringify({ text }) });
          if (d.error) { setError(d.error); return; }
          if (!d.added) {
            setMomentNotice(d.found ? 'Those moments are already in your clips.' : "Couldn't find that moment. Try different wording or a direct quote.");
            return;
          }
          // suggestions refresh via the SSE state-sync broadcast
          setMomentText('');
          setMomentNotice(`Added ${d.added} moment${d.added !== 1 ? 's' : ''}.`);
        } catch {
          setError('Moment search failed.');
        } finally {
          setFindingMoment(false);
        }
      };

      const startExport = async () => {
        setPhase('exporting'); setResults([]);
        const sc = suggestions.filter((_, i) => !deselected.has(i));
        const vp = file?.file_path || videoPath.trim();
        const data = await api('/batch-clips', {
          method: 'POST', body: JSON.stringify({
            video_path: vp,
            clips: sc.map(c => ({ start_second: c.start_second, end_second: c.end_second, title: c.title, caption_style: captionStyle, crop_strategy: cropStrategy, format })),
            transcript_words: transcript?.words || [], logo_path: logoPath || undefined, outro_path: outroPath || undefined, intro_path: introPath || undefined, clean_fillers: cleanFillers || undefined,
          })
        });
        setBatchJobId(data.job_id);
      };

      useEffect(() => {
        if (batchStream?.status === 'done') { setPhase('done'); setResults(batchStream.result?.results || []); setBatchJobId(null); }
        if (batchStream?.status === 'error') { setError('Export failed: ' + batchStream.error); setPhase('review'); setBatchJobId(null); }
      }, [batchStream?.status]);

      const retryClip = async (idx) => {
        const sc = suggestions.filter((_, i) => !deselected.has(i));
        const c = sc[idx]; setRetryIdx(idx); setRetryJobId(null);
        const vp = file?.file_path || videoPath.trim();
        const data = await api('/create-clip', {
          method: 'POST', body: JSON.stringify({
            video_path: vp, start_second: c.start_second, end_second: c.end_second,
            title: c.title, caption_style: captionStyle, crop_strategy: cropStrategy, format,
            transcript_words: transcript?.words || [], logo_path: logoPath || undefined, outro_path: outroPath || undefined, intro_path: introPath || undefined, clean_fillers: cleanFillers || undefined,
          })
        });
        setRetryJobId(data.job_id);
      };

      useEffect(() => {
        if (retryStream?.status === 'done') {
          setResults(prev => { const n = [...prev]; n[retryIdx] = { ...retryStream.result, status: 'success' }; return n; });
          setRetryIdx(null); setRetryJobId(null);
        }
      }, [retryStream?.status]);

      const toggleClip = (i) => setDeselected(prev => { const n = new Set(prev); n.has(i) ? n.delete(i) : n.add(i); return n; });
      const selectedCount = suggestions.length - deselected.size;
      const buildLocalPrompt = () => {
        const parts = [];
        const hasTranscriptReady = transcript || transcriptText.trim();
        if (transcriptMode === 'whisper' && !hasTranscriptReady) {
          parts.push(`Transcribe the podcast at ${videoPath} using transcribe_podcast with model_size="${whisperModel}".`);
          parts.push('Then find the 5-8 best viral-worthy moments and call suggest_clips.');
        } else {
          parts.push('Use get_ui_state with include_transcript=true to read the full transcript.');
          parts.push('Find the 5-8 best viral-worthy moments: hot takes, strong opinions, funny moments, actionable advice, and emotional stories.');
          parts.push('Then call suggest_clips with your suggestions.');
        }
        const settings = [];
        if (videoPath) settings.push(`Video: ${videoPath.split(/[\\/]/).pop()}`);
        settings.push(`Style: ${captionStyle}`);
        settings.push(`Crop: ${cropStrategy}`);
        if (logoPath) settings.push(`Logo: set`);
        if (outroPath) settings.push(`Outro: set`);
        parts.push(`Current settings: ${settings.join(', ')}`);
        return parts.join('\n');
      };

      const copyGeneratePrompt = async () => {
        if (isProcessing) return;
        if (sourceIsUrl) {
          setError('Download the video first, then find best moments.');
          return;
        }
        // If user has pasted a transcript but it hasn't been parsed yet, parse it first
        if (transcriptMode === 'import' && transcriptText.trim() && !transcript) {
          setError(null);
          setPhase('parsing');
          const fileData = await api('/select-file', { method: 'POST', body: JSON.stringify({ file_path: videoPath.trim() }) });
          if (fileData.error) { setError(fileData.error); setPhase('idle'); return; }
          setFile(fileData);
          const text = transcriptText.trim();
          const isJson = text.startsWith('{') || text.startsWith('[');
          let t = null;
          if (isJson) {
            const parsed = JSON.parse(text);
            const tr = Array.isArray(parsed) ? { words: parsed } : parsed;
            const data = await api('/import-transcript', { method: 'POST', body: JSON.stringify({ file_path: videoPath.trim(), transcript: tr }) });
            if (data.error) { setError(data.error); setPhase('idle'); return; }
            t = data.data;
          } else {
            const data = await api('/parse-transcript', { method: 'POST', body: JSON.stringify({ file_path: videoPath.trim(), raw_text: text, time_adjust: timeAdjust }) });
            if (data.error) { setError(data.error); setPhase('idle'); return; }
            t = data.data;
          }
          setTranscript(t);
          // Sync transcript + video to server immediately (don't wait for React useEffect)
          await fetch('/api/ui-state', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ _source: 'ui', transcript: t, videoPath: videoPath.trim(), filePath: fileData.file_path }),
          }).catch(() => { });
          setPhase('idle');
        }

        // Ensure video path is synced even without transcript parsing
        if (videoPath.trim()) {
          await fetch('/api/ui-state', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              _source: 'ui',
              videoPath: videoPath.trim(),
              rawTranscriptText: transcriptText.trim() || undefined,
              settings: { captionStyle, cropStrategy, format, logoPath, outroPath, introPath },
            }),
          }).catch(() => { });
        }

        // Ask Claude directly for clip suggestions (same as CLI)
        setPhase('suggesting');
        setGenerateCopied(false);
        try {
          const res = await fetch('/api/claude-suggest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ top_n: topClips, min_duration: minDuration, max_duration: maxDuration }),
          });
          const data = await res.json();

          if (data.clips && data.clips.length > 0) {
            // Claude returned suggestions — populate directly
            const mapped = data.clips.map((c, i) => ({
              id: `claude-${i}`,
              title: c.title,
              start_second: c.start_second,
              end_second: c.end_second,
              reasoning: c.reasoning || c.content_type || '',
              duration: Math.round((c.end_second || 0) - (c.start_second || 0)),
            }));
            setSuggestions(mapped);
            setEnergyData({});
            setPhase('review');
          } else if (data.fallback === 'clipboard') {
            // Claude not installed — fall back to clipboard copy
            setPhase('idle');
            setError('Claude Code not found. Install it for automatic clip selection, or use MCP.');
            let prompt;
            try {
              const pRes = await fetch('/api/generate-prompt', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'suggest' }),
              });
              const pData = await pRes.json();
              prompt = pData.prompt || '';
            } catch { prompt = ''; }
            if (prompt) {
              navigator.clipboard.writeText(prompt).then(() => {
                setGenerateCopied(true);
                setTimeout(() => setGenerateCopied(false), 3000);
              }).catch(() => { });
            }
          } else {
            setPhase('idle');
            setError(data.error || 'Claude returned no clips');
          }
        } catch (err) {
          setPhase('idle');
          setError('Failed to get suggestions: ' + (err.message || err));
        }
      };

      const isProcessing = phase === 'parsing' || phase === 'suggesting' || phase === 'exporting' || transcribing || downloadingVideo;
      const sourceIsUrl = isHttpUrl(videoPath);
      const selectedClips = suggestions.filter((_, i) => !deselected.has(i));

      const getExportStatus = (resultIdx) => {
        if (phase !== 'exporting' || !batchStream) return null;
        const total = selectedClips.length; if (!total) return null;
        const pct = batchStream.progress || 0;
        const done = Math.floor(pct / (100 / total));
        if (resultIdx < done) return 'exported';
        if (resultIdx === done && pct < 100) return 'rendering';
        return 'pending';
      };

      const handleVideoDrop = (e) => {
        e.preventDefault();
        const f = e.dataTransfer?.files?.[0];
        setUploadedVideo(f);
      };
      const handleTranscriptDrop = (e) => { e.preventDefault(); setTranscriptDragOver(false); const files = e.dataTransfer?.files; if (!files?.length) return; const f = files[0]; setTranscriptFileName(f.name); const reader = new FileReader(); reader.onload = (ev) => setTranscriptText(ev.target.result); reader.readAsText(f); };
      const handleTranscriptFileSelect = (e) => { const f = e.target.files?.[0]; if (!f) return; setTranscriptFileName(f.name); const reader = new FileReader(); reader.onload = (ev) => setTranscriptText(ev.target.result); reader.readAsText(f); };
      const preventDef = (e) => e.preventDefault();

      const activeClip = activeClipIdx !== null ? suggestions[activeClipIdx] : null;

      return (
        <div className="app">
          <PageHeader
            title="Podcast content studio"
            actions={<>
              {mcpConnected && (
                <span className="pill" style={{ fontSize: 10, letterSpacing: '0.5px', background: 'var(--green-subtle)', color: 'var(--green)', border: '1px solid var(--green-border)' }}>
                  MCP linked
                </span>
              )}
              {encoderInfo && (
                <span className="pill pill-blue" style={{ fontSize: 10, letterSpacing: '0.5px' }}>
                  {encoderInfo.best === 'libx264' ? 'CPU' : encoderInfo.best.replace('h264_', '').toUpperCase()}
                </span>
              )}
              {speakerStatus && !speakerStatus.configured && (
                <span className="pill" style={{ fontSize: 10, letterSpacing: '0.5px', background: 'rgba(250,204,21,0.08)', color: '#facc15', border: '1px solid rgba(250,204,21,0.2)', cursor: 'pointer' }}
                  title="Speaker detection not configured. Click to learn more"
                  onClick={() => window.open('https://huggingface.co/pyannote/speaker-diarization-3.1', '_blank')}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>Speakers <X size={11} /></span>
                </span>
              )}
              {speakerStatus && speakerStatus.configured && (
                <span className="pill" style={{ fontSize: 10, letterSpacing: '0.5px', background: 'var(--green-subtle)', color: 'var(--green)', border: '1px solid var(--green-border)' }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>Speakers <Check size={11} /></span>
                </span>
              )}
            </>}
          />

          {speakerStatus && !speakerStatus.configured && !sessionStorage.getItem('dismiss-speaker') && (
            <div className="fade-in" style={{ margin: '0 0 16px', padding: '14px 16px', background: 'rgba(250,204,21,0.06)', border: '1px solid rgba(250,204,21,0.15)', borderRadius: 'var(--radius)', display: 'flex', alignItems: 'flex-start', gap: 12 }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>Set up speaker detection</div>
                <div className="meta">
                  Identify who's talking in your podcast. Free, takes 2 minutes.
                  <br/>
                  <span style={{ color: 'var(--text3)' }}>1.</span> <a href="https://huggingface.co/pyannote/speaker-diarization-3.1" target="_blank" rel="noopener" style={{ color: '#facc15', textDecoration: 'none' }}>Accept model terms</a>
                  {' → '}
                  <span style={{ color: 'var(--text3)' }}>2.</span> <a href="https://huggingface.co/settings/tokens" target="_blank" rel="noopener" style={{ color: '#facc15', textDecoration: 'none' }}>Get free token</a> <span className="hint-xs">(Read permission)</span>
                  {' → '}
                  <span style={{ color: 'var(--text3)' }}>3.</span> Add <code style={{ fontFamily: 'var(--font-mono)', fontSize: 11, padding: '1px 5px', background: 'rgba(250,204,21,0.1)', borderRadius: 4, color: '#facc15' }}>HF_TOKEN=hf_...</code> to your <code style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>.env</code>
                </div>
              </div>
              <button className="icon-btn" onClick={() => { sessionStorage.setItem('dismiss-speaker', '1'); setSpeakerStatus({...speakerStatus, _dismissed: true}); }}
                title="Dismiss"><X size={12} /></button>
            </div>
          )}

          <div className="layout">
            {/* ═══════════ LEFT COLUMN ═══════════ */}
            <div className="main-col">

              {/* Video */}
              <div className="section card">
                <div className="section-label">Video</div>
                {!videoPath && (
                  <div className="drop-zone" style={{ cursor: 'pointer' }} onClick={browsing || isProcessing ? undefined : doBrowse}
                    onDragOver={preventDef} onDrop={handleVideoDrop}>
                    <input ref={videoFileRef} type="file" accept=".mp4,.mov,.mkv,.webm,.mp3,.wav,.m4a" onChange={handleVideoFileSelect} style={{ display: 'none' }} />
                    {browsing ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <div className="spinner sm" />
                        <div style={{ fontSize: 13, fontWeight: 600 }}>Waiting for file selection{'\u2026'}</div>
                      </div>
                    ) : (
                      <>
                        <div className="label"><strong>Browse</strong> to select a video file</div>
                      </>
                    )}
                  </div>
                )}
                {videoPath && !sourceIsUrl && (
                  <div className="file-badge fade-in">
                    <div className="dot" />
                    <div className="name">{videoPath.split(/[\\/]/).pop()}</div>
                    <button className="btn btn-ghost btn-sm" onClick={() => setVideoPath('')} style={{ padding: '4px 10px', fontSize: 11 }}>Clear</button>
                  </div>
                )}
                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  <input type="text" placeholder="Paste a local path or YouTube/video URL"
                    value={videoPath} onChange={e => setVideoPath(e.target.value)}
                    disabled={isProcessing || browsing}
                    onKeyDown={e => { if (e.key === 'Enter' && sourceIsUrl) downloadVideo(); }}
                    style={{ flex: 1, fontSize: 12, padding: '9px 13px', background: 'var(--bg)', borderColor: videoPath ? 'var(--green-border)' : 'var(--border)' }} />
                  <RecentSources onPick={setVideoPath} exclude={videoPath ? [videoPath] : []} />
                  <button className="btn btn-ghost btn-sm" onClick={downloadVideo} disabled={!sourceIsUrl || isProcessing || browsing}>
                    {downloadingVideo ? <div className="spinner sm" /> : <><DownloadGlyph size={14} /> Download</>}
                  </button>
                </div>
                {downloadingVideo && (
                  <div className="progress-track" style={{ marginTop: 6 }}>
                    <div className="progress-fill" style={{ width: `${downloadStream?.progress || 5}%` }} />
                  </div>
                )}
              </div>

              {/* Transcript */}
              <div className="section card">
                <div className="section-label">Transcript</div>
                <div className="tabs">
                  <div className={`tab ${transcriptMode === 'import' ? 'active' : ''}`} onClick={() => setTranscriptMode('import')}>Paste transcript</div>
                  <div className={`tab ${transcriptMode === 'whisper' ? 'active' : ''}`} onClick={() => setTranscriptMode('whisper')}>Auto</div>
                </div>
                {transcriptMode === 'import' && (
                  <div className="fade-in">
                    {!transcriptText && (
                      <div className={`drop-zone ${transcriptDragOver ? 'drag-over' : ''}`}
                        onDragOver={e => { preventDef(e); setTranscriptDragOver(true); }} onDragLeave={() => setTranscriptDragOver(false)}
                        onDrop={handleTranscriptDrop} style={{ marginBottom: 10, padding: '22px 20px' }}>
                        <div className="label">Drop a transcript file or <strong>browse</strong></div>
                        <input type="file" accept=".txt,.json,.srt,.vtt" onChange={handleTranscriptFileSelect} disabled={isProcessing} />
                      </div>
                    )}
                    {transcriptText && transcriptFileName && (
                      <div className="file-badge fade-in" style={{ marginBottom: 10 }}>
                        <div className="dot" /><div className="name">{transcriptFileName}</div>
                        <div className="meta">{transcriptText.split('\n').length} lines</div>
                        <button className="btn btn-ghost btn-sm" onClick={() => { setTranscriptText(''); setTranscriptFileName(''); }} style={{ padding: '4px 10px', fontSize: 11 }}>Clear</button>
                      </div>
                    )}
                    <textarea className="code-input" placeholder={'Speaker (00:00)\nText of what they said...\n\nSpeaker2 (00:15)\nMore text...\n\nOr paste JSON / drag a .txt file above.'}
                      value={transcriptText} onChange={e => { setTranscriptText(e.target.value); setTranscriptFileName(''); }}
                      disabled={isProcessing} style={{ minHeight: transcriptText ? 80 : 120 }}
                      onDragOver={e => { preventDef(e); setTranscriptDragOver(true); }} onDrop={handleTranscriptDrop} />
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 10 }}>
                      <span className="meta" style={{ whiteSpace: 'nowrap' }}>Time offset</span>
                      <input type="number" step="0.5" value={timeAdjust} onChange={e => setTimeAdjust(parseFloat(e.target.value) || 0)}
                        style={{ width: 72 }} disabled={isProcessing} />
                      <span className="hint">sec</span>
                    </div>
                  </div>
                )}
                {transcriptMode === 'whisper' && (
                  <div className="fade-in">
                    <div className="row" style={{ marginBottom: 10 }}>
                      <div>
                        <label className="field-label">Engine</label>
                        <select value={transcriptionEngine} onChange={e => { setTranscriptionEngine(e.target.value); setTranscript(null); setCachedTranscript(false); autoTranscribeRef.current = ''; }} disabled={isProcessing || transcribing}>
                          <option value="whisper">Whisper</option>
                          <option value="assemblyai">AssemblyAI</option>
                        </select>
                      </div>
                      {transcriptionEngine === 'whisper' && (
                        <div>
                          <label className="field-label">Model</label>
                          <select value={whisperModel} onChange={e => setWhisperModel(e.target.value)} disabled={isProcessing || transcribing}>
                            <option value="tiny">Tiny (fastest)</option><option value="base">Base</option>
                            <option value="small">Small</option><option value="medium">Medium</option>
                            <option value="large">Large (best)</option>
                          </select>
                        </div>
                      )}
                    </div>
                    {transcriptionEngine === 'assemblyai' && (
                      <div style={{ marginBottom: 10 }}>
                        <label className="field-label">AssemblyAI API key</label>
                        <input type="password" value={assemblyAiKey} onChange={e => setAssemblyAiKey(e.target.value)}
                          placeholder="aai_..." disabled={isProcessing || transcribing}
                          style={{ fontSize: 12, padding: '9px 13px' }} />
                      </div>
                    )}

                    {/* Transcription progress */}
                    {transcribing && !cachedTranscript && (
                      <div className="fade-in" style={{ padding: '12px 14px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}>
                        <div className="status-line" style={{ fontSize: 12 }}>
                          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <div className="spinner sm" />
                            {transcribeStream?.message || 'Starting transcription\u2026'}
                          </span>
                          {transcribeStream?.progress > 0 && (
                            <span style={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{transcribeStream.progress}%</span>
                          )}
                        </div>
                        {transcribeStream?.progress > 0 && (
                          <div className="progress-track" style={{ marginTop: 6 }}>
                            <div className="progress-fill" style={{ width: `${transcribeStream.progress}%` }} />
                          </div>
                        )}
                        {!transcribeStream?.progress && (
                          <div className="progress-track" style={{ marginTop: 6 }}><div className="progress-fill indeterminate" /></div>
                        )}
                      </div>
                    )}

                    {/* Transcript ready indicator */}
                    {transcript && transcriptMode === 'whisper' && (
                      <div className="file-badge fade-in">
                        <div className="dot" />
                        <div className="name">Transcript ready</div>
                        <div className="meta">
                          {transcript.words?.length || 0} words
                          {transcript.duration && <span> {'\u00B7'} {fmt(transcript.duration)}</span>}
                        </div>
                        {cachedTranscript && (
                          <span className="pill" style={{ fontSize: 10, background: 'var(--blue-subtle)', color: 'var(--blue)', border: '1px solid var(--blue-border)' }}>cached</span>
                        )}
                        <button className="btn btn-ghost btn-sm" onClick={() => { setTranscript(null); setCachedTranscript(false); autoTranscribeRef.current = ''; }} style={{ padding: '4px 10px', fontSize: 11 }}>Re-transcribe</button>
                      </div>
                    )}

                    {!transcript && !transcribing && videoPath.trim() && (
                      <button className="btn btn-ghost btn-sm" onClick={() => { autoTranscribeRef.current = ''; autoTranscribe(videoPath.trim()); }} style={{ marginTop: 4 }}>
                        Transcribe now
                      </button>
                    )}
                  </div>
                )}
              </div>

              {/* Settings */}
              <div className="section card">
                <div className="section-label">Settings</div>

                {/* Presets */}
                {presets.length > 0 && (
                  <div className="preset-bar">
                    <select value={activePreset} onChange={e => loadPreset(e.target.value)} disabled={isProcessing}>
                      <option value="">Load preset…</option>
                      {presets.map(p => <option key={p.name || p} value={p.name || p}>{p.name || p}</option>)}
                    </select>
                    {activePreset && (
                      <button className="btn btn-ghost btn-sm" onClick={() => deletePreset(activePreset)} title="Delete preset" style={{ padding: '6px 8px', color: 'var(--red)' }}><X size={14} /></button>
                    )}
                  </div>
                )}
                {showPresetSave ? (
                  <div className="preset-save-row">
                    <input type="text" placeholder="Preset name" value={presetName} onChange={e => setPresetName(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter') savePreset(); if (e.key === 'Escape') setShowPresetSave(false); }}
                      autoFocus disabled={presetSaving} />
                    <button className="btn btn-primary btn-sm" onClick={savePreset} disabled={!presetName.trim() || presetSaving}>
                      {presetSaving ? <div className="spinner sm" /> : 'Save'}
                    </button>
                    <button className="btn btn-ghost btn-sm" onClick={() => setShowPresetSave(false)}>Cancel</button>
                  </div>
                ) : (
                  <div style={{ marginBottom: 14 }}>
                    <button className="asset-add" onClick={() => setShowPresetSave(true)} disabled={isProcessing}>
                      <Plus size={12} /> Save as preset
                    </button>
                  </div>
                )}

                <div className="settings-grid">
                  <div>
                    <label className="field-label">Caption style</label>
                    <select value={captionStyle} onChange={e => onCaptionChange(e.target.value)} disabled={isProcessing}>
                      <option value="branded">Branded</option><option value="hormozi">Hormozi</option>
                      <option value="karaoke">Karaoke</option><option value="subtle">Subtle</option>
                    </select>
                  </div>
                  <div>
                    <label className="field-label">Crop</label>
                    <select value={cropStrategy} onChange={e => onCropChange(e.target.value)} disabled={isProcessing}>
                      <option value="speaker">Speaker aware</option><option value="face">Face detection</option><option value="center">Center</option>
                    </select>
                  </div>
                  <div>
                    <label className="field-label">Format</label>
                    <select value={format} onChange={e => onFormatChange(e.target.value)} disabled={isProcessing}>
                      <option value="vertical">Vertical 9:16</option><option value="horizontal">Horizontal 16:9</option><option value="square">Square 1:1</option>
                    </select>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                  {captionStyle === 'branded' && (
                    <AssetPicker type="logo" label="Logo" value={logoPath} onChange={setLogoPath} disabled={isProcessing} />
                  )}
                  <AssetPicker type="intro" label="Intro" value={introPath} onChange={setIntroPath} disabled={isProcessing} />
                  <AssetPicker type="outro" label="Outro" value={outroPath} onChange={setOutroPath} disabled={isProcessing} />
                </div>

                {/* Advanced Settings */}
                <div style={{ marginTop: 14 }}>
                  <div style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, fontWeight: 700, letterSpacing: '0.8px', color: 'var(--text2)', textTransform: 'uppercase' }}
                    onClick={() => setAdvancedOpen(!advancedOpen)}>
                    <span style={{ transition: 'transform 0.15s', transform: advancedOpen ? 'rotate(90deg)' : 'rotate(0deg)', display: 'inline-flex' }}><ChevronRight size={12} /></span>
                    Advanced
                  </div>
                  {advancedOpen && (
                    <div className="advanced-grid fade-in">
                      <div className="field">
                        <label className="field-label">Quality</label>
                        <select value={quality} onChange={e => setQuality(e.target.value)} disabled={isProcessing}>
                          <option value="max">Max</option><option value="high">High</option><option value="fast">Fast</option>
                        </select>
                      </div>
                      <div className="field">
                        <label className="field-label">Top clips</label>
                        <div className="field-row">
                          <input type="range" min="3" max="15" value={topClips} onChange={e => setTopClips(parseInt(e.target.value))} />
                          <span className="range-value">{topClips}</span>
                        </div>
                      </div>
                      <div className="field">
                        <label className="field-label">Min duration</label>
                        <div className="field-row">
                          <input type="range" min="10" max="60" step="5" value={minDuration} onChange={e => setMinDuration(parseInt(e.target.value))} />
                          <span className="range-value">{minDuration}s</span>
                        </div>
                      </div>
                      <div className="field">
                        <label className="field-label">Max duration</label>
                        <div className="field-row">
                          <input type="range" min="30" max="60" step="5" value={maxDuration} onChange={e => setMaxDuration(parseInt(e.target.value))} />
                          <span className="range-value">{maxDuration}s</span>
                        </div>
                      </div>
                      <div className="toggle-row" style={{ gridColumn: '1 / -1' }}>
                        <span className="toggle-label">Clean filler words (um, uh, hmm)</span>
                        <div className={`toggle ${cleanFillers ? 'on' : ''}`} onClick={() => setCleanFillers(!cleanFillers)} />
                      </div>
                      <div className="toggle-row" style={{ gridColumn: '1 / -1', paddingTop: 0 }}>
                        <span className="toggle-label">Energy boost (normalize loud moments)</span>
                        <div className={`toggle ${energyBoost ? 'on' : ''}`} onClick={() => setEnergyBoost(!energyBoost)} />
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* Word Corrections */}
              <div className="section">
                <div className="section-label" style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6 }} onClick={() => setCorrectionsOpen(!correctionsOpen)}>
                  <span style={{ transition: 'transform 0.15s', transform: correctionsOpen ? 'rotate(90deg)' : 'rotate(0deg)', display: 'inline-flex' }}><ChevronRight size={12} /></span>
                  Word Corrections
                  {Object.keys(corrections).length > 0 && (
                    <span className="hint-xs" style={{ fontWeight: 400 }}>({Object.keys(corrections).length})</span>
                  )}
                </div>
                {correctionsOpen && (
                  <div className="fade-in" style={{ marginTop: 8 }}>
                    <div className="hint" style={{ marginBottom: 8, lineHeight: 1.4 }}>
                      Fix Whisper misheard words. Applied automatically to all transcripts.
                    </div>
                    <div style={{ display: 'flex', gap: 6, marginBottom: 8, alignItems: 'center' }}>
                      <input value={correctionWord} onChange={e => setCorrectionWord(e.target.value)}
                        placeholder="Wrong (e.g. Boxel)" style={{ flex: 1 }} />
                      <span style={{ color: 'var(--text3)', display: 'inline-flex', flexShrink: 0 }}><ArrowRight size={12} /></span>
                      <input value={correctionFix} onChange={e => setCorrectionFix(e.target.value)}
                        placeholder="Correct (e.g. Voxel)" style={{ flex: 1 }}
                        onKeyDown={e => { if (e.key === 'Enter' && correctionWord.trim() && correctionFix.trim()) {
                          api('/corrections/add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ wrong: correctionWord.trim(), correct: correctionFix.trim() }) })
                            .then(d => { if (d.corrections) { setCorrections(d.corrections); setCorrectionWord(''); setCorrectionFix(''); } });
                        }}} />
                      <button className="btn btn-ghost btn-sm" disabled={!correctionWord.trim() || !correctionFix.trim()} onClick={() => {
                        api('/corrections/add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ wrong: correctionWord.trim(), correct: correctionFix.trim() }) })
                          .then(d => { if (d.corrections) { setCorrections(d.corrections); setCorrectionWord(''); setCorrectionFix(''); } });
                      }} style={{ fontSize: 11, padding: '6px 12px' }}>Add</button>
                    </div>
                    {Object.keys(corrections).length > 0 && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                        {Object.entries(corrections).map(([wrong, correct]) => (
                          <div key={wrong} className="asset-pill" style={{ fontSize: 11 }}>
                            <span style={{ color: 'var(--text3)', textDecoration: 'line-through' }}>{wrong}</span>
                            <span style={{ color: 'var(--text3)', margin: '0 2px', display: 'inline-flex' }}><ArrowRight size={12} /></span>
                            <span style={{ color: 'var(--green)' }}>{correct}</span>
                            <button className="asset-pill-x" onClick={() => {
                              fetch(`/api/corrections/${encodeURIComponent(wrong)}`, { method: 'DELETE' })
                                .then(r => r.json()).then(d => { if (d.corrections) setCorrections(d.corrections); });
                            }}><X size={12} /></button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Error */}
              {error && (
                <div className="error-bar fade-in">
                  <span>{error}</span>
                  <button className="btn btn-ghost btn-sm" onClick={() => setError(null)} style={{ flexShrink: 0, color: 'var(--red)', borderColor: 'var(--red-border)' }}>Dismiss</button>
                </div>
              )}

              {/* Generate */}
              {phase === 'idle' && (
                <div>
                  {sourceIsUrl && (
                    <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>
                      Download the video first, then find best moments.
                    </div>
                  )}
                  <button className="btn btn-go"
                    disabled={isProcessing || sourceIsUrl || !videoPath.trim() || (transcriptMode === 'import' && !transcriptText.trim()) || (transcriptMode === 'whisper' && !transcript) || browsing || transcribing}
                    onClick={copyGeneratePrompt}
                    style={generateCopied ? { background: 'var(--green)', transition: 'background 0.2s' } : {}}>
                    {phase === 'suggesting' ? 'Claude is analyzing...' : generateCopied ? 'Copied. Paste in Claude' : 'Find best moments'}
                  </button>
                  {videoPath.trim() && (transcriptText.trim() || transcript) && (
                    <McpHints phase={phase} videoPath={videoPath} transcript={transcript} transcriptText={transcriptText} suggestions={suggestions} mcpConnected={mcpConnected} />
                  )}
                </div>
              )}

              {/* Find a specific moment: paste a quote/description, AI locates it */}
              {(transcript || transcriptText) && phase !== 'parsing' && phase !== 'suggesting' && phase !== 'exporting' && (
                <div className="section" style={{ marginTop: 16 }}>
                  <div className="section-label">Find a specific moment</div>
                  <textarea
                    className="input"
                    rows={3}
                    placeholder="Paste a quote or describe the moment you want. The AI searches the transcript and adds it to your clips."
                    value={momentText}
                    onChange={(e) => { setMomentText(e.target.value); setMomentNotice(null); }}
                    disabled={findingMoment}
                    style={{ width: '100%', resize: 'vertical' }}
                  />
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 8 }}>
                    <button className="btn btn-primary btn-sm" onClick={findMoment} disabled={!momentText.trim() || findingMoment}>
                      {findingMoment
                        ? <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}><div className="spinner sm" />Searching{'…'}</span>
                        : 'Find moments'}
                    </button>
                    {momentNotice && <span style={{ color: 'var(--text2)', fontSize: 13 }}>{momentNotice}</span>}
                  </div>
                </div>
              )}

              {/* Parsing */}
              {phase === 'parsing' && (
                <div className="fade-in" style={{ marginTop: 20 }}>
                  <div className="status-line"><span style={{ display: 'flex', alignItems: 'center', gap: 8 }}><div className="spinner sm" />Processing transcript{'\u2026'}</span></div>
                  <div className="progress-track"><div className="progress-fill indeterminate" /></div>
                </div>
              )}

              {/* Suggesting (no clips yet) */}
              {phase === 'suggesting' && suggestions.length === 0 && (
                <div className="fade-in" style={{ marginTop: 20 }}>
                  <div className="status-line"><span style={{ display: 'flex', alignItems: 'center', gap: 8 }}><div className="spinner sm" />Analyzing transcript{'\u2026'}</span></div>
                  <div className="progress-track"><div className="progress-fill indeterminate" /></div>
                </div>
              )}

              {/* Clip list */}
              {(phase === 'suggesting' || phase === 'review' || phase === 'exporting' || phase === 'done') && suggestions.length > 0 && (
                <div>
                  <div className="spacer" />
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
                    <div className="section-label" style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 8, fontVariantNumeric: 'tabular-nums' }}>
                      {phase === 'suggesting' && <div className="spinner sm" />}
                      {phase === 'suggesting' ? `Found ${suggestions.length} clip${suggestions.length !== 1 ? 's' : ''}`
                        : phase === 'review' ? `Clips \u00B7 ${selectedCount} selected` : 'Clips'}
                    </div>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                      {phase === 'review' && videoPath && (
                        <button className="btn btn-ghost btn-sm" onClick={analyzeEnergy} disabled={analyzingEnergy || suggestions.length === 0} title="Analyze audio energy levels">
                          {analyzingEnergy ? <><div className="spinner sm" /> Analyzing…</> : <><Activity size={14} /> Energy</>}
                        </button>
                      )}
                      {phase === 'review' && (
                        <button className="btn btn-primary btn-sm" disabled={selectedCount === 0} onClick={startExport} style={{ fontVariantNumeric: 'tabular-nums' }}>
                          Export {selectedCount} clip{selectedCount !== 1 ? 's' : ''}
                        </button>
                      )}
                      {transcript && (phase === 'review' || phase === 'done') && (
                        <div style={{ position: 'relative' }}>
                          <button className="btn btn-ghost btn-sm overflow-menu-btn" onClick={e => { const m = e.currentTarget.nextElementSibling; m.style.display = m.style.display === 'block' ? 'none' : 'block'; }} style={{ padding: '6px 10px', fontSize: 14, color: 'var(--text3)', lineHeight: 1 }}>
                            {'\u22EF'}
                          </button>
                          <div className="overflow-menu" style={{ display: 'none' }}>
                            <div className="overflow-menu-label">Export transcript</div>
                            {['SRT', 'VTT', 'JSON'].map(fmt => (
                              <button key={fmt} className="overflow-menu-item" onClick={e => { window.open(`/api/export-transcript?format=${fmt.toLowerCase()}`, '_blank'); e.currentTarget.closest('.overflow-menu').style.display = 'none'; }}>
                                {fmt}
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>

                  {suggestions.map((clip, i) => {
                    const off = deselected.has(i);
                    const resultIdx = [...suggestions.keys()].filter(k => !deselected.has(k)).indexOf(i);
                    const r = results[resultIdx];
                    const outputFile = r?.output_path?.split('/').pop();
                    const failed = r?.status === 'error';
                    const isRetryingThis = retryIdx === resultIdx;
                    const exportStatus = !off ? getExportStatus(resultIdx) : null;
                    const isSelected = activeClipIdx === i && !previewSrc;

                    return (
                      <div key={i}
                        className={`clip-item ${off && phase === 'review' ? 'dimmed' : ''} ${exportStatus === 'rendering' ? 'active-clip' : ''} ${phase === 'suggesting' ? 'clip-reveal' : ''} ${isSelected ? 'selected' : ''}`}
                        onClick={() => onClipClick(i)}>

                        {phase === 'review' && (
                          <div className={`checkbox ${!off ? 'checked' : ''}`} onClick={(e) => { e.stopPropagation(); toggleClip(i); }}>
                            {!off && <CheckIcon />}
                          </div>
                        )}

                        {phase === 'exporting' && !off && exportStatus && (
                          <div className={`clip-status ${exportStatus}`}>{exportStatus === 'exported' && <CheckSmall />}</div>
                        )}

                        {phase === 'done' && !off && r && (
                          <div className={`status-dot ${failed ? 'fail' : 'ok'}`}>{failed ? <X size={11} /> : <Check size={11} />}</div>
                        )}

                        <div className="clip-info">
                          <div className="clip-title">{clip.title}</div>
                          <div className="clip-meta">
                            {fmt(clip.start_second)} {'\u2192'} {fmt(clip.end_second)} {'\u00B7'} {clip.duration}s
                            {energyData[i] && (
                              <span className={`energy-badge ${energyData[i].level}`} title={`Energy: ${energyData[i].score}/10`}>
                                {energyData[i].level === 'high' ? <Activity size={10} /> : energyData[i].level === 'medium' ? '~' : '○'} {energyData[i].score.toFixed(1)}
                              </span>
                            )}
                            {r && !failed && <span> {'\u00B7'} {r.file_size_mb}MB</span>}
                            {failed && <span className="err"> {'\u00B7'} {r.error?.slice(0, 60)}</span>}
                            {exportStatus === 'rendering' && <span style={{ color: 'var(--accent)' }}> {'\u00B7'} rendering{'\u2026'}</span>}
                          </div>
                        </div>

                        {phase === 'review' && (
                          <button className="btn btn-ghost btn-sm clip-edit-btn" onClick={(e) => openClipEdit(i, e)} title="Edit clip"><Pencil size={13} /></button>
                        )}

                        {phase === 'done' && !off && r && !failed && outputFile && (
                          <div className="clip-actions" onClick={e => e.stopPropagation()}>
                            <button className="btn btn-ghost btn-sm" onClick={() => onPlayRendered(outputFile)} title="Preview"><Play size={13} /></button>
                            <a href={`/api/download/${outputFile}`} className="btn btn-primary btn-sm" download title="Download"><Download size={14} /></a>
                            <button className="btn btn-ghost btn-sm" disabled={isRetryingThis} onClick={() => retryClip(resultIdx)} title="Retry"><RotateCcw size={13} /></button>
                          </div>
                        )}
                        {phase === 'done' && !off && r && failed && (
                          <button className="btn btn-ghost btn-sm" onClick={e => { e.stopPropagation(); retryClip(resultIdx); }} disabled={isRetryingThis}>
                            {isRetryingThis ? '\u2026' : 'Retry'}
                          </button>
                        )}
                      </div>
                    );
                  })}

                  {phase === 'exporting' && batchStream && (
                    <div style={{ marginTop: 14 }}>
                      <div className="status-line">
                        <span>{batchStream.message}</span>
                        <span style={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{batchStream.progress || 0}%</span>
                      </div>
                      <div className="progress-track"><div className="progress-fill" style={{ width: `${batchStream.progress || 0}%` }} /></div>
                    </div>
                  )}

                  {retryIdx !== null && retryStream?.status === 'running' && (
                    <div style={{ marginTop: 10, padding: 12, background: 'var(--surface)', borderRadius: 'var(--radius-sm)' }}>
                      <div className="status-line" style={{ fontSize: 12 }}>
                        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}><div className="spinner sm" />Retrying{'\u2026'}</span>
                        <span style={{ fontWeight: 600 }}>{retryStream.progress || 0}%</span>
                      </div>
                      <div className="progress-track"><div className="progress-fill" style={{ width: `${retryStream.progress || 0}%` }} /></div>
                    </div>
                  )}

                  {(phase === 'done' || phase === 'review' || phase === 'exporting') && (
                    <div style={{ display: 'flex', gap: 10, justifyContent: 'center', marginTop: 24 }}>
                      <button className="btn btn-ghost" onClick={() => {
                        setPhase('idle'); setResults([]); setSuggestions([]); setBatchJobId(null); setFile(null); setTranscript(null); setActiveClipIdx(null); setPreviewSrc(null); setEnergyData({}); setCachedTranscript(false); autoTranscribeRef.current = '';
                        fetch('/api/ui-state', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ _source: 'ui', phase: 'idle', suggestions: [], deselectedIndices: [] }) }).catch(() => { });
                      }}>Start over</button>
                      {phase === 'done' && <button className="btn btn-ghost" onClick={() => { setPhase('review'); setResults([]); setBatchJobId(null); }}>Re-export</button>}
                    </div>
                  )}
                  {(phase === 'review' || phase === 'done') && (
                    <McpHints phase={phase} videoPath={videoPath} transcript={transcript} transcriptText={transcriptText} suggestions={suggestions} mcpConnected={mcpConnected} />
                  )}

                  {phase === 'done' && (
                    <div className="fade-in" style={{ marginTop: 20, padding: 16, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                        <div style={{ width: 20, height: 20, borderRadius: 6, background: 'rgba(74,222,128,0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700, color: 'var(--green)' }}>P</div>
                        <span style={{ fontSize: 13, fontWeight: 700 }}>PodStack: next steps</span>
                      </div>
                      <p className="meta" style={{ lineHeight: 1.6, marginBottom: 12 }}>
                        Clips are rendered. Now generate titles, descriptions, and thumbnails in Claude Code:
                      </p>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                        {[
                          { cmd: '/prep-episode', desc: 'Full pipeline: titles + descriptions + thumbnails' },
                          { cmd: '/generate-titles', desc: '8 title options per clip' },
                          { cmd: '/publish-checklist', desc: 'Pre-upload optimization checklist' },
                        ].map(item => (
                          <div key={item.cmd} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', background: 'var(--bg)', borderRadius: 'var(--radius-sm)', fontSize: 12 }}>
                            <code style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--accent)', whiteSpace: 'nowrap' }}>{item.cmd}</code>
                            <span style={{ color: 'var(--text3)' }}>{item.desc}</span>
                          </div>
                        ))}
                      </div>
                      <a href="https://github.com/nmbrthirteen/podstack" target="_blank" rel="noopener"
                        style={{ display: 'inline-block', marginTop: 10, fontSize: 11, color: 'var(--text3)', textDecoration: 'none' }}>
                        github.com/nmbrthirteen/podstack →
                      </a>
                    </div>
                  )}
                </div>
              )}
              {/* ═══════════ CLIP HISTORY ═══════════ */}
              {clipHistory.length > 0 && (
                <div className="section" style={{ marginTop: 16 }}>
                  <div className="section-label" style={{ cursor: 'pointer', userSelect: 'none', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
                    onClick={() => setHistoryOpen(!historyOpen)}>
                    <span>History ({clipHistory.length})</span>
                    <span className="hint-xs" style={{ transition: 'transform 0.2s', transform: historyOpen ? 'rotate(180deg)' : 'rotate(0)' }}><ChevronDown size={12} /></span>
                  </div>
                  {historyOpen && (
                    <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 300, overflowY: 'auto', marginTop: 8 }}>
                      {clipHistory.map((c, i) => {
                        const date = new Date(c.created_at);
                        const ago = ((Date.now() - date.getTime()) / 3600000);
                        const timeStr = ago < 1 ? `${Math.round(ago * 60)}m ago` : ago < 24 ? `${Math.round(ago)}h ago` : date.toLocaleDateString();
                        const fname = c.output_path?.split('/').pop() || c.title;
                        return (
                          <div key={c.id || i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', background: 'var(--surface)', borderRadius: 'var(--radius-sm)', fontSize: 12, cursor: 'pointer' }}
                            onClick={() => { const f = c.output_path?.split('/').pop(); if (f) setPreviewSrc(f); }}>
                            <div style={{ width: 6, height: 6, borderRadius: 3, background: 'var(--green)', flexShrink: 0 }} />
                            <div style={{ flex: 1, minWidth: 0 }}>
                              <div style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.title || fname}</div>
                              <div className="hint" style={{ marginTop: 2 }}>
                                {c.duration}s {'\u00B7'} {c.file_size_mb?.toFixed(1)}MB {'\u00B7'} {c.caption_style} {'\u00B7'} {timeStr}
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* ═══════════ RIGHT COLUMN — PREVIEW ═══════════ */}
            <div className="preview-col">
              <div className="preview-panel">

                {/* Old video player - used ONLY for playing a rendered clip.
                    Source-video preview now lives inside <LivePhonePreview/>. */}
                {videoUrl && previewSrc && (
                  <div className="preview-player fade-in" style={{ marginBottom: 12 }}>
                    <video
                      key={videoUrl}
                      ref={videoRef}
                      src={videoUrl}
                      controls
                      preload="auto"
                      className={previewSrc ? 'vertical' : ''}
                    />
                    {activeClip && !previewSrc && (
                      <div className="preview-clip-range">
                        <span className="clip-name">{activeClip.title}</span>
                        <span>{fmt(activeClip.start_second)} {'\u2192'} {fmt(activeClip.end_second)}</span>
                      </div>
                    )}
                    {previewSrc && (
                      <div className="preview-clip-range">
                        <span className="clip-name">Rendered clip</span>
                        <button className="preview-back" onClick={() => setPreviewSrc(null)}>Back to source</button>
                      </div>
                    )}
                  </div>
                )}

                {/* Style preview mockup — hidden when rendered clip is playing */}
                {!previewSrc && (
                  <LivePhonePreview
                    videoUrl={videoUrl}
                    videoRef={videoRef}
                    captionStyle={captionStyle}
                    activeClip={activeClip}
                    transcriptWords={transcript ? transcript.words : null}
                    logoPath={logoPath}
                    previewSrc={previewSrc}
                    showTikTokFrame={showTikTokFrame}
                    onToggleFrame={() => setShowTikTokFrame(v => !v)}
                  />
                )}
                <SpecRecap
                  captionStyle={captionStyle}
                  cropStrategy={cropStrategy}
                  logoPath={logoPath}
                  outroPath={outroPath}
                  activePreset={activePreset}
                  quality={quality}
                  cleanFillers={cleanFillers}
                />
              </div>
            </div>
          </div>

          {/* Clip Edit Modal */}
          {editingClip !== null && suggestions[editingClip] && createPortal(
            <div className="clip-edit-overlay" onClick={() => setEditingClip(null)}>
              <div className="clip-edit-panel" onClick={e => e.stopPropagation()}>
                <h3>Edit clip #{editingClip + 1}</h3>
                <div className="edit-field">
                  <label>Title</label>
                  <input type="text" value={editForm.title} onChange={e => setEditForm(f => ({ ...f, title: e.target.value }))}
                    onKeyDown={e => { if (e.key === 'Enter') saveClipEdit(); }} autoFocus />
                </div>
                <div className="edit-field">
                  <label>Time range</label>
                  <div className="time-row">
                    <div>
                      <input type="number" step="0.5" value={editForm.start} onChange={e => setEditForm(f => ({ ...f, start: parseFloat(e.target.value) || 0 }))}
                        style={{ textAlign: 'center' }} />
                      <div className="hint-xs" style={{ textAlign: 'center', marginTop: 2 }}>{fmt(editForm.start)}</div>
                    </div>
                    <div className="arrow"><ArrowRight size={14} /></div>
                    <div>
                      <input type="number" step="0.5" value={editForm.end} onChange={e => setEditForm(f => ({ ...f, end: parseFloat(e.target.value) || 0 }))}
                        style={{ textAlign: 'center' }} />
                      <div className="hint-xs" style={{ textAlign: 'center', marginTop: 2 }}>{fmt(editForm.end)}</div>
                    </div>
                  </div>
                  <div className="hint" style={{ marginTop: 6, textAlign: 'center' }}>
                    Duration: {Math.round(editForm.end - editForm.start)}s
                  </div>
                </div>
                <div className="clip-edit-actions">
                  <button className="btn btn-ghost btn-sm" onClick={deleteClipEdit} style={{ color: 'var(--red)', marginRight: 'auto' }}>Delete clip</button>
                  <button className="btn btn-ghost btn-sm" onClick={() => setEditingClip(null)}>Cancel</button>
                  <button className="btn btn-primary btn-sm" onClick={saveClipEdit} disabled={!editForm.title.trim() || editForm.end <= editForm.start}>Save</button>
                </div>
              </div>
            </div>
          , document.body)}

          {/* Modal (mobile fallback) */}
          {previewFile && createPortal(
            <div className="modal-overlay" onClick={() => setPreviewFile(null)}>
              <div className="modal-body" onClick={e => e.stopPropagation()}>
                <video src={`/api/preview/${previewFile}`} controls autoPlay />
                <div style={{ textAlign: 'center', marginTop: 12 }}>
                  <button className="btn btn-ghost btn-sm" onClick={() => setPreviewFile(null)}>Close</button>
                </div>
              </div>
            </div>
          , document.body)}
        </div>
      );
    }
