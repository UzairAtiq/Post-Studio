import React from "react";
import { Badge } from "@/components/ui/badge";

interface StatusBadgeProps {
  status?: string;
  className?: string;
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status = "pending", className }) => {
  const normalized = status.toLowerCase().trim();

  let variant: "pending" | "approved" | "discarded" | "secondary" = "pending";
  let label = "Pending";

  if (normalized === "approved" || normalized === "published") {
    variant = "approved";
    label = normalized === "published" ? "Published" : "Approved";
  } else if (
    normalized === "discarded" ||
    normalized === "publish_failed"
  ) {
    variant = "discarded";
    label = normalized === "publish_failed" ? "Failed" : "Discarded";
  } else {
    variant = "pending";
    label = normalized === "pending_approval" ? "Pending Approval" : "Pending";
  }

  return (
    <Badge variant={variant} className={className}>
      {label}
    </Badge>
  );
};
