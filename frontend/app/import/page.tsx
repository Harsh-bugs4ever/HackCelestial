"use client";

import { useEffect, useRef, useState } from "react";
import { api, type DatasetSpec, type ImportResult } from "@/lib/api";
import { useSession } from "@/components/SessionProvider";
import { ErrorNote, Section, Spinner } from "@/components/ui";
import { Field } from "@/components/Dialog";

/**
 * The supported path from a property's own systems into the data spine.
 *
 * Every engine trains on Layer 1, and Layer 1 could previously only be filled
 * by the seed generator - which made the platform a demo of itself. CSV is the
 * deliberate choice over a PMS connector: Opera, Cloudbeds and Micros each need
 * their own auth and field mapping, and a half-built connector is worse than an
 * export every property already knows how to produce.
 */
export default function ImportPage() {
  const [datasets, setDatasets] = useState<DatasetSpec[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [history, setHistory] = useState<(ImportResult & { at: string | null })[]>([]);
  const [busy, setBusy] = useState<"dry" | "real" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const { can } = useSession();
  const mayImport = can("data:import");

  useEffect(() => {
    api
      .importDatasets()
      .then((d) => {
        setDatasets(d.datasets);
        setSelected((current) => current || d.datasets[0]?.name || "");
      })
      .catch((e) => setLoadError(e instanceof Error ? e.message : String(e)));
    refreshHistory();
  }, []);

  function refreshHistory() {
    api.importHistory().then((h) => setHistory(h.batches)).catch(() => {});
  }

  const spec = datasets.find((d) => d.name === selected);

  async function upload(dryRun: boolean) {
    if (!file || !selected) return;
    setBusy(dryRun ? "dry" : "real");
    setError(null);
    setResult(null);
    try {
      const outcome = await api.importCsv(selected, file, dryRun);
      setResult(outcome);
      refreshHistory();
      if (!dryRun && outcome.rows_written > 0) {
        // Clear the picker so the same file is not imported twice by reflex.
        setFile(null);
        if (fileInput.current) fileInput.current.value = "";
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (loadError) return <ErrorNote error={loadError} />;
  if (!datasets.length) return <Spinner label="Loading datasets" />;

  return (
    <div className="flex flex-col gap-6">
      <Section
        title="Import operating data"
        description="Load a CSV export from your PMS, POS, HRMS or BMS into the data spine. Always dry-run first."
      >
        {!mayImport && (
          <div
            className="card px-4 py-3 text-[12.5px]"
            style={{ borderLeft: "3px solid var(--status-warning)" }}
          >
            Your role cannot import data. Ask a general manager to run this, or sign in with an
            account that has the permission.
          </div>
        )}

        <div className="card p-4 flex flex-col gap-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Field label="Dataset" hint={spec?.note}>
              <select
                className="input"
                value={selected}
                onChange={(e) => {
                  setSelected(e.target.value);
                  setResult(null);
                }}
              >
                {datasets.map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.name.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="CSV file" hint={file ? `${(file.size / 1024).toFixed(0)} KB selected` : "UTF-8 or Excel export"}>
              <input
                ref={fileInput}
                type="file"
                accept=".csv,text/csv"
                className="input"
                onChange={(e) => {
                  setFile(e.target.files?.[0] ?? null);
                  setResult(null);
                }}
              />
            </Field>
          </div>

          {spec && (
            <div className="text-[12px] flex flex-col gap-1">
              <div>
                <span style={{ color: "var(--text-muted)" }}>Required columns: </span>
                <span className="font-medium">{spec.required.join(", ")}</span>
              </div>
              {spec.optional.length > 0 && (
                <div>
                  <span style={{ color: "var(--text-muted)" }}>Optional: </span>
                  <span>{spec.optional.join(", ")}</span>
                </div>
              )}
              <a
                className="underline underline-offset-2 w-fit"
                style={{ color: "var(--text-secondary)" }}
                href={api.templateUrl(spec.name)}
                target="_blank"
                rel="noreferrer"
              >
                Download a blank template →
              </a>
            </div>
          )}

          <div className="flex gap-2 flex-wrap items-center">
            <button
              className="btn-ghost px-4 py-2 text-[13px] font-medium disabled:opacity-50"
              disabled={!file || busy !== null || !mayImport}
              onClick={() => upload(true)}
            >
              {busy === "dry" ? "Checking…" : "Dry run"}
            </button>
            <button
              className="btn-approve px-4 py-2 text-[13px] disabled:opacity-50"
              disabled={!file || busy !== null || !mayImport}
              onClick={() => upload(false)}
            >
              {busy === "real" ? "Importing…" : "Import for real"}
            </button>
            <span className="text-[12px]" style={{ color: "var(--text-muted)" }}>
              A dry run validates every row and writes nothing.
            </span>
          </div>

          {error && (
            <p role="alert" className="text-[13px]" style={{ color: "var(--status-critical)" }}>
              {error}
            </p>
          )}

          {result && <ImportOutcome result={result} />}
        </div>
      </Section>

      {history.length > 0 && (
        <Section title="Import history" description="Who loaded what, and whether it landed cleanly.">
          <div className="card overflow-x-auto">
            <table className="w-full text-[12.5px] min-w-[620px]">
              <thead>
                <tr style={{ color: "var(--text-muted)" }}>
                  <th className="text-left font-medium px-3 py-2">When</th>
                  <th className="text-left font-medium px-3 py-2">Dataset</th>
                  <th className="text-left font-medium px-3 py-2">File</th>
                  <th className="text-left font-medium px-3 py-2">By</th>
                  <th className="text-right font-medium px-3 py-2">Seen</th>
                  <th className="text-right font-medium px-3 py-2">Written</th>
                  <th className="text-right font-medium px-3 py-2">Skipped</th>
                </tr>
              </thead>
              <tbody>
                {history.map((b) => (
                  <tr key={b.id} style={{ borderTop: "1px solid var(--border)" }}>
                    <td className="px-3 py-2 tabular">
                      {b.at ? new Date(b.at).toLocaleString() : "—"}
                    </td>
                    <td className="px-3 py-2">
                      {b.dataset.replace(/_/g, " ")}
                      {b.dry_run && (
                        <span className="ml-1.5 text-[11px]" style={{ color: "var(--text-muted)" }}>
                          (dry run)
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 truncate max-w-[160px]">{b.filename || "—"}</td>
                    <td className="px-3 py-2">{b.imported_by || "—"}</td>
                    <td className="px-3 py-2 text-right tabular">{b.rows_seen}</td>
                    <td className="px-3 py-2 text-right tabular">{b.rows_written}</td>
                    <td
                      className="px-3 py-2 text-right tabular"
                      style={{ color: b.rows_skipped ? "var(--status-warning)" : undefined }}
                    >
                      {b.rows_skipped}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}

function ImportOutcome({ result }: { result: ImportResult }) {
  const clean = result.ok && result.rows_skipped === 0;
  const accent = clean ? "var(--status-good)" : "var(--status-warning)";
  return (
    <div
      className="rounded-md px-3 py-2.5 text-[12.5px] flex flex-col gap-2"
      style={{ background: "var(--page-plane)", borderLeft: `3px solid ${accent}` }}
    >
      <div className="font-semibold">
        {result.dry_run ? "Dry run — nothing was written." : `Imported ${result.rows_written} row(s).`}
      </div>
      <div style={{ color: "var(--text-secondary)" }}>
        {result.rows_seen} row(s) read
        {result.rows_skipped > 0 && `, ${result.rows_skipped} skipped`}
        {result.dry_run && result.rows_seen > result.rows_skipped &&
          ` — ${result.rows_seen - result.rows_skipped} would be written.`}
      </div>
      {result.errors.length > 0 && (
        <div className="flex flex-col gap-1">
          <div className="font-medium">Problems found:</div>
          <ul className="flex flex-col gap-0.5">
            {result.errors.map((e, i) => (
              <li key={i} style={{ color: "var(--text-secondary)" }}>
                {e.row > 0 ? `Row ${e.row}: ` : ""}
                {e.error}
              </li>
            ))}
          </ul>
          {result.errors.length >= 50 && (
            <div style={{ color: "var(--text-muted)" }}>
              Only the first 50 problems are listed.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
