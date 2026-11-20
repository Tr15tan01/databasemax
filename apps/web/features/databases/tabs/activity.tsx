"use client";
import { useQuery } from "@tanstack/react-query";
import * as React from "react";

import { Card } from "@/components/ui/card";
import { Pagination } from "@/components/ui/pagination";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { eventLabel } from "@/lib/labels";
import type { Database, DbEvent, Page } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

export function ActivityTab({ database }: { database: Database }) {
  const [page, setPage] = React.useState(1);
  const q = useQuery({ queryKey: ["db-events", database.id, "page", page], queryFn: () => api<Page<DbEvent>>(`/databases/${database.id}/events`, { query: { page, page_size: 25 } }) });
  return (
    <>
      <Card>
        {!q.data ? <div className="p-5"><Skeleton className="h-40" /></div> : (
          <Table>
            <THead><TR><TH>Event</TH><TH>Details</TH><TH>By</TH><TH>When</TH></TR></THead>
            <TBody>
              {q.data.items.map((e) => (
                <TR key={e.id}>
                  <TD className="font-medium">{eventLabel(e)}</TD>
                  <TD className="max-w-md text-muted">{e.message}</TD>
                  <TD className="capitalize text-muted">{e.actor_type.replace("_", " ")}</TD>
                  <TD className="whitespace-nowrap text-muted">{formatDateTime(e.created_at)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Card>
      {q.data && <Pagination page={page} pageSize={25} total={q.data.total} onPage={setPage} />}
    </>
  );
}
