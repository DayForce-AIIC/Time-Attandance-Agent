import { Link } from "react-router-dom";
import type { ExceptionOut } from "../api/schemas";
import { EmptyState, formatDateShort } from "./common";
import { SeverityBadge, StatusBadge } from "./common";

export function ExceptionTable({ exceptions }: { exceptions: ExceptionOut[] }) {
  if (exceptions.length === 0) {
    return <EmptyState title="No exceptions need review." detail="Your current filters returned no timecard cases." />;
  }
  return (
    <div className="table-scroll">
      <table className="data-table exception-table">
        <thead>
          <tr>
            <th scope="col">Severity</th>
            <th scope="col">Employee / reference</th>
            <th scope="col">Work date</th>
            <th scope="col">Rule</th>
            <th scope="col">Status</th>
            <th scope="col"><span className="visually-hidden">Review link</span></th>
          </tr>
        </thead>
        <tbody>
          {exceptions.map((exception) => (
            <tr key={exception.id}>
              <td><SeverityBadge severity={exception.severity} /></td>
              <td>
                <strong>{exception.employee_name}</strong>
                {exception.employee_name === exception.employee_id
                  ? <small className="table-subtext">Company employee reference</small>
                  : <small className="table-subtext">{exception.employee_id}</small>}
              </td>
              <td>{formatDateShort(exception.work_date)}</td>
              <td>
                <span className="rule-code">{exception.rule_id}</span>
                <small className="table-subtext">{exception.title}</small>
              </td>
              <td><StatusBadge status={exception.status} /></td>
              <td><Link className="table-link" to={`/exceptions/${exception.id}`}>Review <span aria-hidden="true">→</span></Link></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
