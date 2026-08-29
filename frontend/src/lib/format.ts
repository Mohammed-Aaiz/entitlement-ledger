export function formatINR(amount: number): string {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(amount);
}

export function formatDate(iso: string): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

export function formatDateTime(iso: string): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function sourceTypeLabel(type: string): string {
  const labels: Record<string, string> = {
    order: 'Order Record',
    delivery: 'Delivery Record',
    complaint: 'Customer Complaint',
    policy_doc: 'Policy Document',
    seller_agreement: 'Seller Agreement',
    refund_record: 'Refund Record',
  };
  return labels[type] || type;
}

export function sourceTypeColor(type: string): string {
  const colors: Record<string, string> = {
    order: 'bg-blue-100 text-blue-800',
    delivery: 'bg-purple-100 text-purple-800',
    complaint: 'bg-red-100 text-red-800',
    policy_doc: 'bg-amber-100 text-amber-800',
    seller_agreement: 'bg-green-100 text-green-800',
    refund_record: 'bg-orange-100 text-orange-800',
  };
  return colors[type] || 'bg-gray-100 text-gray-800';
}
