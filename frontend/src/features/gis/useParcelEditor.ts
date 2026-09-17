import { useState } from "react";
import type { Parcel } from "../../api/gis";
import { basicPolygonProblems, cloneGeometry, insertVertex, isEditablePolygon, nearestEdgeIndex, removeVertex, replaceVertex, type PolygonGeometry, type Position } from "./geometry";

export interface ParcelEditSession {
  parcelId: string;
  expectedCurrentVersion: number;
  original: PolygonGeometry;
  working: PolygonGeometry;
  history: PolygonGeometry[];
  historyIndex: number;
  selectedVertex: number | null;
  showOriginal: boolean;
}

export function useParcelEditor() {
  const [session, setSession] = useState<ParcelEditSession | null>(null);

  const enter = (parcel: Parcel): string | null => {
    if (parcel.current_version.geometry?.type === "MultiPolygon") return "MultiPolygon editing is not supported in this D.2 MVP. The geometry remains read-only.";
    if (parcel.current_version.geometry?.type === "Polygon" && !isEditablePolygon(parcel.current_version.geometry)) return "Polygon geometries with interior rings are not editable in this D.2 MVP. The geometry remains read-only.";
    if (!isEditablePolygon(parcel.current_version.geometry)) return "This parcel has no editable world Polygon geometry.";
    const original = cloneGeometry(parcel.current_version.geometry);
    setSession({ parcelId: parcel.id, expectedCurrentVersion: parcel.current_geometry_version, original, working: cloneGeometry(original), history: [cloneGeometry(original)], historyIndex: 0, selectedVertex: null, showOriginal: true });
    return null;
  };

  const commit = (next: PolygonGeometry, selectedVertex: number | null = null) => setSession((current) => {
    if (!current) return current;
    const history = [...current.history.slice(0, current.historyIndex + 1), cloneGeometry(next)];
    return { ...current, working: cloneGeometry(next), history, historyIndex: history.length - 1, selectedVertex };
  });

  const moveVertex = (index: number, position: Position) => setSession((current) => {
    if (!current) return current;
    const next = replaceVertex(current.working, index, position);
    return { ...current, working: next, selectedVertex: index };
  });

  // Drag updates are coalesced; append one undo step when the pointer is released.
  const commitDraggedVertex = () => setSession((current) => {
    if (!current) return current;
    const history = [...current.history.slice(0, current.historyIndex + 1), cloneGeometry(current.working)];
    return { ...current, history, historyIndex: history.length - 1 };
  });

  const addVertexAt = (position: Position) => setSession((current) => {
    if (!current) return current;
    const next = insertVertex(current.working, nearestEdgeIndex(current.working, position), position);
    const history = [...current.history.slice(0, current.historyIndex + 1), cloneGeometry(next)];
    return { ...current, working: next, history, historyIndex: history.length - 1, selectedVertex: null };
  });

  const deleteSelectedVertex = (): string | null => {
    if (!session || session.selectedVertex === null) return "Select a vertex before deleting it.";
    const next = removeVertex(session.working, session.selectedVertex);
    if (!next) return "A parcel boundary cannot have fewer than three vertices.";
    commit(next); return null;
  };

  const undo = () => setSession((current) => current && current.historyIndex > 0 ? { ...current, historyIndex: current.historyIndex - 1, working: cloneGeometry(current.history[current.historyIndex - 1]), selectedVertex: null } : current);
  const redo = () => setSession((current) => current && current.historyIndex < current.history.length - 1 ? { ...current, historyIndex: current.historyIndex + 1, working: cloneGeometry(current.history[current.historyIndex + 1]), selectedVertex: null } : current);
  const reset = () => setSession((current) => current ? { ...current, working: cloneGeometry(current.original), history: [cloneGeometry(current.original)], historyIndex: 0, selectedVertex: null } : current);
  const selectVertex = (index: number | null) => setSession((current) => current ? { ...current, selectedVertex: index } : current);
  const toggleOriginal = () => setSession((current) => current ? { ...current, showOriginal: !current.showOriginal } : current);

  return { session, enter, cancel: () => setSession(null), saved: () => setSession(null), moveVertex, commitDraggedVertex, addVertexAt, deleteSelectedVertex, undo, redo, reset, selectVertex, toggleOriginal, problems: session ? basicPolygonProblems(session.working) : [] };
}
