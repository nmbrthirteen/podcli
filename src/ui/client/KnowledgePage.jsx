import React, { useState, useEffect, useCallback } from 'react';
import { PageHeader } from "./Page";
import { Trash2 } from 'lucide-react';
import { api, upload } from './lib';
import CopyButton from './CopyButton';

const BOOTSTRAP_COMMAND = 'podcli bootstrap-knowledge <channel-url>';

export default function KnowledgePage() {
      const [files, setFiles] = useState([]);
      const [activeFile, setActiveFile] = useState(null);
      const [editorContent, setEditorContent] = useState('');
      const [dirty, setDirty] = useState(false);
      const [dragOver, setDragOver] = useState(false);
      const [toast, setToast] = useState(null);
      const [newName, setNewName] = useState('');
      const [kbDir, setKbDir] = useState('');
      const [creating, setCreating] = useState(false);
      const [created, setCreated] = useState(null);

      const showToast = (msg) => { setToast(msg); setTimeout(() => setToast(null), 2000); };

      const loadFiles = useCallback(async () => {
        try { const r = await api('/knowledge'); setFiles(Array.isArray(r) ? r : []); }
        catch (e) { showToast(`Load failed: ${e.message}`); }
      }, []);

      useEffect(() => {
        loadFiles();
        api('/knowledge/dir').then(d => setKbDir(d.path)).catch(() => { });
      }, []);

      const openFile = (f) => {
        if (dirty && !confirm('Discard unsaved changes?')) return;
        setActiveFile(f.filename);
        setEditorContent(f.content);
        setDirty(false);
      };

      const saveFile = async () => {
        if (!activeFile) return;
        try {
          await api(`/knowledge/${encodeURIComponent(activeFile)}`, { method: 'POST', body: JSON.stringify({ content: editorContent }) });
          setDirty(false);
          showToast('Saved');
          loadFiles();
        } catch (e) { showToast(`Save failed: ${e.message}`); }
      };

      const deleteFile = async (filename, e) => {
        e.stopPropagation();
        if (!confirm(`Delete ${filename}?`)) return;
        try {
          await api(`/knowledge/${encodeURIComponent(filename)}`, { method: 'DELETE' });
          if (activeFile === filename) { setActiveFile(null); setEditorContent(''); }
          showToast('Deleted');
          loadFiles();
        } catch (e) { showToast(`Delete failed: ${e.message}`); }
      };

      const createFile = async () => {
        let name = newName.trim();
        if (!name) return;
        if (!name.endsWith('.md')) name += '.md';
        try {
          await api(`/knowledge/${encodeURIComponent(name)}`, { method: 'POST', body: JSON.stringify({ content: `# ${name.replace('.md', '')}\n\n` }) });
          setNewName('');
          await loadFiles();
          const created = (await api('/knowledge')).find(f => f.filename === name);
          if (created) openFile(created);
          showToast('Created');
        } catch (e) { showToast(`Create failed: ${e.message}`); }
      };

      const createStarterFiles = async () => {
        setCreating(true);
        try {
          const r = await api('/knowledge/init', { method: 'POST' });
          setCreated(r.created || []);
          await loadFiles();
        } catch (e) { showToast(`Create failed: ${e.message}`); }
        finally { setCreating(false); }
      };

      const uploadFiles = async (fileList, label) => {
        const fd = new FormData();
        for (const f of fileList) {
          if (f.name.endsWith('.md') || f.name.endsWith('.txt')) fd.append('files', f);
        }
        if (!fd.has('files')) return;
        try {
          await upload('/knowledge/upload', fd);
          showToast(`${label} added`);
          loadFiles();
        } catch (e) { showToast(`Upload failed: ${e.message}`); }
      };

      const handleDrop = async (e) => {
        e.preventDefault();
        setDragOver(false);
        if (!e.dataTransfer.files.length) return;
        await uploadFiles(e.dataTransfer.files, `${e.dataTransfer.files.length} file(s)`);
      };

      const handleFileInput = async (e) => {
        if (!e.target.files.length) return;
        await uploadFiles(e.target.files, `${e.target.files.length} file(s)`);
        e.target.value = '';
      };

      const realFiles = files.filter(f => f.filename !== 'README.md');

      return (
        <div className="app">
          <PageHeader
            title="Knowledge base"
            actions={kbDir ? <div className="dir-path">{kbDir}</div> : null}
          />

          <div
            className={`drop-zone knowledge-drop-zone ${dragOver ? 'drag-over' : ''}`}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
          >
            <div className="dz-text">
              Drop <strong>.md files</strong> here or click to browse
            </div>
            <input type="file" accept=".md,.txt" multiple onChange={handleFileInput} />
          </div>

          {created?.length > 0 && (
            <div className="set-note ok" style={{ marginBottom: 16 }}>
              Created {created.length} files. Fill in <strong>01-brand-identity.md</strong> and <strong>02-voice-and-tone.md</strong> first: the clip scorer reads those two on every run.
            </div>
          )}

          {realFiles.length > 0 ? (
            <div className="file-grid">
              {realFiles.map(f => (
                <div
                  key={f.filename}
                  className={`file-card ${activeFile === f.filename ? 'active' : ''}`}
                  onClick={() => openFile(f)}
                >
                  <button className="file-delete" onClick={(e) => deleteFile(f.filename, e)} title="Delete"><Trash2 size={13} /></button>
                  <div className="file-name">{f.filename}</div>
                  <div className="file-meta">{new Date(f.updatedAt).toLocaleDateString()}</div>
                  <div className="file-preview">{f.content.split('\n').filter(l => l.trim() && !l.startsWith('#')).slice(0, 2).join(' ')}</div>
                </div>
              ))}
            </div>
          ) : (
            <div className="section card" style={{ marginTop: 16 }}>
              <div className="section-label">Start here</div>
              <div className="meta" style={{ marginBottom: 14 }}>
                podcli reads 14 numbered files when it picks clips and writes titles. Create the starter set, then fill in the [brackets].
              </div>
              <button className="btn btn-primary" onClick={createStarterFiles} disabled={creating}>
                {creating ? <div className="spinner sm" /> : 'Create starter templates'}
              </button>
              <div className="code-block">
                <div className="code-block-head">
                  <span>or draft them from a channel you already run</span>
                  <CopyButton className="btn btn-ghost btn-sm" style={{ padding: '3px 10px' }} text={BOOTSTRAP_COMMAND} />
                </div>
                <pre>{BOOTSTRAP_COMMAND}</pre>
              </div>
            </div>
          )}

          <div className="new-file-row">
            <input
              className="new-file-input"
              placeholder="new-file-name.md"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && createFile()}
            />
            <button className="btn btn-primary btn-sm" onClick={createFile}>Create</button>
          </div>

          {activeFile && (
            <div className="editor-panel" style={{ animation: 'fadeIn 0.2s var(--ease)' }}>
              <div className="editor-header">
                <span className="editor-filename">{activeFile}</span>
                <div className="editor-actions">
                  {dirty && <span style={{ fontSize: 11, color: 'var(--accent)', fontWeight: 600, padding: '4px 8px' }}>unsaved</span>}
                  <button className="btn btn-ghost btn-sm" onClick={() => { setActiveFile(null); setEditorContent(''); setDirty(false); }}>Close</button>
                  <button className="btn btn-primary btn-sm" onClick={saveFile}>Save</button>
                </div>
              </div>
              <textarea
                className="editor-textarea"
                value={editorContent}
                onChange={e => { setEditorContent(e.target.value); setDirty(true); }}
              />
            </div>
          )}

          {toast && <div className="toast">{toast}</div>}
        </div>
      );
    }
