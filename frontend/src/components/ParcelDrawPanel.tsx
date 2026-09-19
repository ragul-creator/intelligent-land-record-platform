import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ApiError, createGeoAIJob } from "../api/gis";
import { geodesicAreaM2, squareFeet } from "../features/gis/geometry";
import { useParcelDraw } from "../features/gis/useParcelDraw";

const format = (value: number | null) => value === null ? "Not available" : new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);

export function ParcelDrawPanel({ projectId, draw, canEdit, onSaved }: { projectId: string; draw: ReturnType<typeof useParcelDraw>; canEdit: boolean; onSaved: () => Promise<void> }) {
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: () => createGeoAIJob(projectId, { job_type: "PARCEL_IMPORT", source_type: "HUMAN_DRAWN", source_payload: { geometry: draw.geometry, notes: reason.trim() || null }, source_crs: "EPSG:4326", source_reference: null, idempotency_key: `human-drawn:${crypto.randomUUID()}` }),
    onSuccess: async () => { setMessage("Draft parcel queued for authoritative validation and versioned persistence."); draw.cancel(); setReason(""); await onSaved(); },
    onError: (error) => setMessage(error instanceof ApiError ? error.message : "The draft parcel could not be queued."),
  });
  const area = draw.geometry ? geodesicAreaM2(draw.geometry) : null;
  if (!canEdit) return <section className="parcel-editor" aria-label="New parcel drawing controls"><p className="panel-note">You have read-only access. `geo:edit_draft` is required to draw a draft parcel.</p></section>;
  if (draw.points === null) return <section className="parcel-editor" aria-label="New parcel drawing controls"><button type="button" className="primary-action" onClick={draw.start}>Draw New Parcel</button><p className="panel-note">Click the map to add boundary points. The result is a human-drawn draft, not a legal parcel determination.</p>{message && <p className="success-copy" role="status">{message}</p>}</section>;
  return <section className="parcel-editor edit-mode" aria-label="New parcel drawing controls"><p className="edit-banner">Draw mode · Click the map to add boundary points.</p><p>{draw.points.length} point{draw.points.length === 1 ? "" : "s"} selected</p><div className="area-comparison"><div><strong>Preview area</strong><span>{format(area)} m² · {format(squareFeet(area))} sq ft</span></div></div><label className="reason-field">Drawing note (optional)<textarea value={reason} onChange={(event) => setReason(event.target.value)} maxLength={2000} placeholder="Human-drawn draft boundary" /></label><div className="editor-actions"><button type="button" onClick={draw.undo} disabled={!draw.points.length}>Undo point</button><button type="button" className="primary-action" onClick={() => mutation.mutate()} disabled={!draw.geometry || Boolean(draw.problems.length) || mutation.isPending}>Save Draft Parcel</button><button type="button" onClick={draw.cancel} disabled={mutation.isPending}>Cancel</button></div>{draw.problems.length > 0 && <p className="error-copy">{draw.problems[0]}</p>}{message && <p className="success-copy" role="status">{message}</p>}</section>;
}
