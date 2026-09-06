import { AgentsView, GenericView, PixelOffice, Shell, TableView } from "../components";

export default async function ViewPage({ params }: { params: Promise<{ view: string }> }) {
  const { view } = await params;
  let content = <GenericView view={view}/>;
  if (view === "ai-agents") content = <AgentsView/>;
  if (view === "pixel-office") content = <PixelOffice/>;
  if (view === "orders" || view === "positions") content = <TableView type={view}/>;
  return <Shell>{content}</Shell>;
}
