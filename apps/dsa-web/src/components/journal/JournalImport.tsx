import type React from 'react';
import { useState } from 'react';
import { useJournalStore } from '../../stores/journalStore';

export const JournalImport: React.FC<{ onImported?: () => void }> = ({ onImported }) => {
  const importCsv = useJournalStore((s) => s.importCsv);
  const importing = useJournalStore((s) => s.importing);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);

  const handleChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    setError(null);
    setResult(null);
    try {
      const resp = await importCsv(file);
      setResult(resp.message);
      onImported?.();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    }
    // Reset the input so the same file can be re-selected.
    event.target.value = '';
  };

  return (
    <div className="card-base p-4">
      <h3 className="text-lg font-semibold">Import Broker CSV</h3>
      <p className="mt-1 text-sm text-muted">
        当前支持 Moomoo US 导出 CSV。已导入过的文件（SHA-256 相同）会被跳过。
      </p>
      <div className="mt-4 flex items-center gap-3">
        <label className="btn-primary cursor-pointer">
          <input
            type="file"
            accept=".csv,text/csv"
            className="hidden"
            onChange={handleChange}
            disabled={importing}
          />
          {importing ? 'Uploading…' : 'Choose CSV'}
        </label>
        {fileName && <span className="text-sm text-muted">{fileName}</span>}
      </div>
      {result && <p className="mt-3 rounded bg-emerald-500/10 px-3 py-2 text-sm text-emerald-400">{result}</p>}
      {error && <p className="mt-3 rounded bg-red-500/10 px-3 py-2 text-sm text-red-400">{error}</p>}
    </div>
  );
};

export default JournalImport;
