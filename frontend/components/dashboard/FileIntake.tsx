"use client";

import { Check, CircleX, FileDown, FileSpreadsheet, Upload } from "lucide-react";
import { useState } from "react";
import type { FileVerdict } from "@/lib/dashboardState";

/**
 * One of the two files, in whatever state it is in: nothing chosen, chosen,
 * or read and judged. Drag and drop as well as a button, because a
 * spreadsheet usually arrives in a folder already open next to the browser.
 *
 * The input keeps its `aria-label` of "Load.xlsx file" - it is how the file
 * is named to anyone not looking at the screen, and how the tests find it.
 */
export function FileIntake({
  title,
  hint,
  file,
  verdict,
  onChange,
  disabled,
  heading,
  templateHref,
  chooseLabel = "Choose File",
}: {
  title: string;
  hint: string;
  file: File | null;
  verdict?: FileVerdict;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  disabled: boolean;
  /** What the file is, above its name: "Teaching Data". */
  heading?: string;
  /** Where a blank template with example rows can be downloaded. */
  templateHref?: string;
  chooseLabel?: string;
}) {
  const [over, setOver] = useState(false);
  const inputRef = useState<HTMLInputElement | null>(null)[0];
  const internalInputRef = useState<HTMLInputElement | null>(null);
  const dragCounter = useState(0);

  function drop(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    setOver(false);
    if (disabled) return;
    const dropped = e.dataTransfer.files?.[0];
    if (!dropped) return;
    onChange({ target: { files: e.dataTransfer.files } } as unknown as React.ChangeEvent<HTMLInputElement>);
  }

  function handleCardClick(e: React.MouseEvent) {
    const target = e.target as HTMLElement;
    if (
      target.closest("a") ||
      target.closest("button") ||
      target.tagName === "INPUT" ||
      target.tagName === "LABEL"
    ) {
      return;
    }
    if (!disabled && inputElement) {
      inputElement.click();
    }
  }

  const [inputElement, setInputElement] = useState<HTMLInputElement | null>(null);

  const edge = over
    ? "border-accent bg-accent-surface ring-2 ring-accent"
    : file
      ? "border-line bg-surface"
      : "border-dashed border-line-strong bg-surface hover:border-accent hover:bg-surface-hover";

  return (
    <div
      onClick={handleCardClick}
      onDragEnter={(e) => {
        e.preventDefault();
        e.stopPropagation();
        if (!disabled) setOver(true);
      }}
      onDragOver={(e) => {
        e.preventDefault();
        e.stopPropagation();
        if (!disabled) {
          e.dataTransfer.dropEffect = "copy";
          setOver(true);
        }
      }}
      onDragLeave={(e) => {
        e.preventDefault();
        e.stopPropagation();
        setOver(false);
      }}
      onDrop={drop}
      className={`rounded-md border p-4 transition-colors select-none ${edge} ${
        !disabled ? "cursor-pointer" : "cursor-not-allowed opacity-80"
      }`}
    >
      <div className="flex items-start gap-3">
        <span
          className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-md ${
            file ? "bg-accent-surface text-accent" : "bg-surface-hover text-ink-muted"
          }`}
        >
          <FileSpreadsheet aria-hidden="true" size={18} />
        </span>

        <div className="min-w-0 flex-1">
          {heading && (
            <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">
              {heading}
            </p>
          )}
          <h3 className="text-sm font-semibold text-ink">{title}</h3>
          <p className="mt-0.5 text-xs text-ink-muted">{hint}</p>

          {file ? (
            <p className="mt-2 truncate text-sm font-medium text-ink" title={file.name}>
              {file.name}
              <span className="ml-2 font-normal text-ink-faint">{size(file.size)}</span>
            </p>
          ) : (
            <p className="mt-2 text-sm text-ink-muted">No file selected</p>
          )}

          {verdict && file && (
            <p
              className={`mt-1 flex items-center gap-1.5 text-sm ${
                verdict.ok ? "text-ok" : "text-blocker"
              }`}
            >
              {verdict.ok ? (
                <Check aria-hidden="true" size={14} />
              ) : (
                <CircleX aria-hidden="true" size={14} />
              )}
              {verdict.detail}
            </p>
          )}

          <label
            className={`mt-3 inline-flex h-9 items-center gap-1.5 rounded-md border px-3 text-sm font-medium transition-colors ${
              disabled
                ? "cursor-not-allowed border-line text-ink-faint"
                : "cursor-pointer border-line-strong text-ink-soft hover:bg-surface-hover"
            }`}
          >
            <Upload aria-hidden="true" size={14} />
            {file ? "Replace file" : chooseLabel}
            <input
              ref={setInputElement}
              type="file"
              accept=".xlsx,.csv"
              className="sr-only"
              onClick={(e) => {
                (e.target as HTMLInputElement).value = "";
              }}
              onChange={onChange}
              disabled={disabled}
              aria-label={`${title} file`}
            />
          </label>
          <span className="ml-2 hidden text-xs text-ink-faint sm:inline">or drop it here</span>
          {templateHref && (
            <a
              href={templateHref}
              download
              className="mt-1 flex min-h-9 w-fit items-center gap-1 text-xs font-medium text-accent underline-offset-2 hover:underline"
            >
              <FileDown aria-hidden="true" size={13} />
              Download the template
            </a>
          )}
        </div>
      </div>
    </div>
  );
}

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
