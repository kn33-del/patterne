import { useEffect, useState, startTransition, type ChangeEvent } from "react";
import { useNavigate } from "react-router-dom";

import { analyzeDcp, listDcps, registerLocalDcp, uploadDcp, type DcpRecord } from "../lib/api";
import { formatBytes, formatDate } from "../lib/format";

export function UploadDcpPage() {
  const [dcps, setDcps] = useState<DcpRecord[]>([]);
  const [localPath, setLocalPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  async function refresh() {
    const rows = await listDcps();
    startTransition(() => {
      setDcps(rows);
    });
  }

  useEffect(() => {
    void refresh().catch((loadError) => {
      setError(loadError instanceof Error ? loadError.message : "Failed to load DCP registry.");
    });
  }, []);

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await uploadDcp(file);
      await refresh();
      event.target.value = "";
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRegisterLocal() {
    setBusy(true);
    setError(null);
    try {
      await registerLocalDcp(localPath);
      setLocalPath("");
      await refresh();
    } catch (registerError) {
      setError(registerError instanceof Error ? registerError.message : "Local registration failed.");
    } finally {
      setBusy(false);
    }
  }

  async function handleAnalyze(dcpId: string) {
    setBusy(true);
    setError(null);
    try {
      const run = await analyzeDcp(dcpId);
      navigate(`/runs/${run.run_id}`);
    } catch (analyzeError) {
      setError(analyzeError instanceof Error ? analyzeError.message : "Analyze request failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page-grid">
      <section className="panel section-stack">
        <div className="section-heading section-heading-inline">
          <h1>Upload DCP</h1>
        </div>
        {error ? <p className="error-text">{error}</p> : null}
        <div className="two-col-grid">
          <label className="upload-dropzone">
            <span>Upload file</span>
            <input type="file" accept=".dcp" disabled={busy} onChange={handleUpload} />
          </label>
          <div className="inline-card">
            <span className="field-label">Use local path</span>
            <input
              className="text-input"
              placeholder="/abs/path/to/design.dcp"
              value={localPath}
              onChange={(event) => setLocalPath(event.target.value)}
            />
            <button className="button primary" onClick={handleRegisterLocal} disabled={busy || !localPath.trim()}>
              Register
            </button>
          </div>
        </div>
      </section>

      <section className="panel section-stack">
        <div className="section-heading section-heading-inline">
          <h2>Recent DCPs</h2>
        </div>
        {dcps.length === 0 ? (
          <p className="muted">No DCPs registered yet.</p>
        ) : (
          <div className="table-shell">
            <table className="data-table">
              <thead>
                <tr>
                  <th>DCP</th>
                  <th>Source</th>
                  <th>Size</th>
                  <th>Added</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {dcps.map((dcp) => (
                  <tr key={dcp.dcp_id}>
                    <td>
                      <div>{dcp.filename}</div>
                      <div className="table-subtext">{dcp.path}</div>
                    </td>
                    <td>{dcp.kind}</td>
                    <td>{formatBytes(dcp.size_bytes)}</td>
                    <td>{formatDate(dcp.registered_at)}</td>
                    <td>
                      <button className="button primary" onClick={() => handleAnalyze(dcp.dcp_id)} disabled={busy}>
                        Analyze
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
