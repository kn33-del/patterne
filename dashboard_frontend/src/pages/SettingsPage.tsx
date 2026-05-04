import { useEffect, useState, startTransition } from "react";

import { getSettings, type SettingsResponse } from "../lib/api";

export function SettingsPage() {
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void getSettings()
      .then((payload) => {
        if (!cancelled) {
          startTransition(() => setSettings(payload));
        }
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Failed to load settings.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="page-grid">
      <section className="panel section-stack">
        <div className="section-heading section-heading-inline">
          <h1>Settings</h1>
        </div>
        {error ? <p className="error-text">{error}</p> : null}
        {!settings ? (
          <p className="muted">Loading settings...</p>
        ) : (
          <div className="table-shell">
            <table className="data-table">
              <tbody>
                <tr>
                  <th>Product</th>
                  <td>{settings.product_name}</td>
                </tr>
                <tr>
                  <th>Repo root</th>
                  <td>{settings.repo_root}</td>
                </tr>
                <tr>
                  <th>Data root</th>
                  <td>{settings.data_root}</td>
                </tr>
                <tr>
                  <th>Python</th>
                  <td>{settings.python_executable}</td>
                </tr>
                <tr>
                  <th>Java home</th>
                  <td>{settings.java_home}</td>
                </tr>
                <tr>
                  <th>optimizer.py</th>
                  <td>{settings.optimizer_script_present ? "Ready" : "Missing"}</td>
                </tr>
                <tr>
                  <th>AI key</th>
                  <td>{settings.openrouter_api_key_configured ? "Configured" : "Missing"}</td>
                </tr>
                <tr>
                  <th>Autopilot</th>
                  <td>{settings.ai_autopilot_ready ? "Ready" : "Needs API key"}</td>
                </tr>
                <tr>
                  <th>Frontend build</th>
                  <td>{settings.frontend_dist_present ? "Present" : "Missing"}</td>
                </tr>
                <tr>
                  <th>Preflight</th>
                  <td>{settings.startup_preflight_enabled ? "Enabled" : "Skipped"}</td>
                </tr>
                <tr>
                  <th>Recipes</th>
                  <td>{settings.enabled_recipes.join(", ")}</td>
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
