import { Link } from "react-router-dom";

export function BrandMark() {
  return (
    <Link className="brand-mark" to="/" aria-label="Bhumi-AI home">
      <span className="brand-earth" aria-hidden="true">
        <span className="brand-earth-map" />
      </span>
      <span className="brand-wordmark">Bhumi-AI</span>
    </Link>
  );
}
