import React, { useState, useEffect, useCallback } from 'react';
import { api, upload } from './lib';

export default function KnowledgePage() {
      const [files, setFiles] = useState([]);
      const [activeFile, setActiveFile] = useState(null);
      const [editorContent, setEditorContent] = useState('');
      const [dirty, setDirty] = useState(false);
      const [dragOver, setDragOver] = useState(false);
      const [toast, setToast] = useState(null);
      const [newName, setNewName] = useState('');
      const [kbDir, setKbDir] = useState('');

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
          <div className="header">
            <h1>Knowledge Base</h1>
            {kbDir && <div className="dir-path">{kbDir}</div>}
          </div>

          <div
            className={`drop-zone ${dragOver ? 'drag-over' : ''}`}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
          >
            <div className="dz-icon">{'\uD83D\uDCC4'}</div>
            <div className="dz-text">
              Drop <strong>.md files</strong> here or click to browse
            </div>
            <input type="file" accept=".md,.txt" multiple onChange={handleFileInput} />
          </div>

          {realFiles.length > 0 ? (
            <div className="file-grid">
              {realFiles.map(f => (
                <div
                  key={f.filename}
                  className={`file-card ${activeFile === f.filename ? 'active' : ''}`}
                  onClick={() => openFile(f)}
                >
                  <button className="file-delete" onClick={(e) => deleteFile(f.filename, e)}>{'\u00D7'}</button>
                  <div className="file-name">{f.filename}</div>
                  <div className="file-meta">{new Date(f.updatedAt).toLocaleDateString()}</div>
                  <div className="file-preview">{f.content.split('\n').filter(l => l.trim() && !l.startsWith('#')).slice(0, 2).join(' ')}</div>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty-state">
              No knowledge files yet. Drop .md files above or create one below.
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
            <button className="btn btn-primary" onClick={createFile}>Create</button>
          </div>

          {activeFile && (
            <div className="editor-panel" style={{ animation: 'fadeIn 0.2s var(--ease)' }}>
              <div className="editor-header">
                <span className="editor-filename">{activeFile}</span>
                <div className="editor-actions">
                  {dirty && <span style={{ fontSize: 11, color: 'var(--accent)', fontWeight: 600, padding: '4px 8px' }}>unsaved</span>}
                  <button className="btn btn-ghost" onClick={() => { setActiveFile(null); setEditorContent(''); setDirty(false); }}>Close</button>
                  <button className="btn btn-primary" onClick={saveFile}>Save</button>
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
