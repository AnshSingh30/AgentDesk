"use client";

import { UploadCloud } from "lucide-react";
import * as React from "react";
import { useDropzone } from "react-dropzone";
import { toast } from "sonner";

import { Progress } from "@/components/ui/display";
import { errorMessage } from "@/lib/api";
import { bytes, cn } from "@/lib/utils";

export interface PresignedUpload {
  upload_url: string;
  headers: Record<string, string>;
  key?: string;
  document_id?: string;
  name?: string;
  mime?: string;
  size?: number;
}

/** Uploads a file straight to S3/MinIO with a presigned PUT (PRD KB-3) and reports progress. */
export function putPresigned(
  file: File,
  presigned: PresignedUpload,
  onProgress?: (pct: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", presigned.upload_url);
    for (const [k, v] of Object.entries(presigned.headers)) {
      if (k.toLowerCase() !== "content-length") xhr.setRequestHeader(k, v);
    }
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => (xhr.status < 300 ? resolve() : reject(new Error(`Upload failed (${xhr.status})`)));
    xhr.onerror = () => reject(new Error("Upload failed (network)"));
    xhr.send(file);
  });
}

export function Dropzone({
  accept,
  maxSizeMb,
  multiple = true,
  onFiles,
  hint,
  className,
  busy,
}: {
  accept: Record<string, string[]>;
  maxSizeMb: number;
  multiple?: boolean;
  onFiles: (files: File[]) => Promise<void> | void;
  hint?: string;
  className?: string;
  busy?: boolean;
}) {
  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept,
    multiple,
    maxSize: maxSizeMb * 1024 * 1024,
    disabled: busy,
    onDropRejected: (rejections) => {
      for (const r of rejections) toast.error(`${r.file.name}: ${r.errors.map((e) => e.message).join(", ")}`);
    },
    onDropAccepted: async (files) => {
      try {
        await onFiles(files);
      } catch (e) {
        toast.error(errorMessage(e));
      }
    },
  });
  return (
    <div
      {...getRootProps()}
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed border-border p-8 text-center transition-colors hover:border-primary",
        isDragActive && "border-primary bg-primary-soft",
        busy && "pointer-events-none opacity-60",
        className,
      )}
    >
      <input {...getInputProps()} aria-label="Upload files" />
      <UploadCloud className="mb-2 h-8 w-8 text-muted-foreground" />
      <p className="text-sm font-medium">
        {isDragActive ? "Drop to upload" : "Drag & drop files, or click to browse"}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">{hint ?? `Up to ${maxSizeMb} MB per file`}</p>
    </div>
  );
}

export function UploadProgressList({
  items,
}: {
  items: { name: string; size: number; pct: number; error?: string }[];
}) {
  if (!items.length) return null;
  return (
    <ul className="mt-3 space-y-2">
      {items.map((it) => (
        <li key={it.name} className="text-xs">
          <div className="flex justify-between">
            <span className="truncate">{it.name}</span>
            <span className="text-muted-foreground">{it.error ?? `${bytes(it.size)} · ${it.pct}%`}</span>
          </div>
          <Progress value={it.pct} className="mt-1" label={`Upload ${it.name}`} />
        </li>
      ))}
    </ul>
  );
}
