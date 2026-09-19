import { useState } from "react";
import { basicPolygonProblems, polygonFromOpenRing, type PolygonGeometry, type Position } from "./geometry";

export function useParcelDraw() {
  const [points, setPoints] = useState<Position[] | null>(null);
  const start = () => setPoints([]);
  const addPoint = (point: Position) => setPoints((current) => current === null ? current : [...current, point]);
  const undo = () => setPoints((current) => current?.slice(0, -1) ?? current);
  const cancel = () => setPoints(null);
  const geometry: PolygonGeometry | null = points && points.length >= 3 ? polygonFromOpenRing(points) : null;
  return { points, geometry, start, addPoint, undo, cancel, problems: geometry ? basicPolygonProblems(geometry) : ["Add at least three points to complete a parcel boundary."] };
}
