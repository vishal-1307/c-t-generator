/**
 * How a room is named anywhere a person has to pick one.
 *
 * A room is identified by (block, floor, room_number), so "101" names a
 * different room in every block. Listing the bare number produces a menu with
 * the same label repeated - on the deployed demo estate the master
 * timetable's room filter showed "101" six times, "102" six times and "201"
 * seven times, none of them distinguishable.
 *
 * `room_code` ("36-101") is the label the institution actually uses and is
 * unambiguous on its own, so it wins wherever it is set. It is optional in the
 * schema (the importer derives it from block + number when a sheet leaves it
 * blank), hence the fallback.
 */
export function roomLabel(room: {
  room_number: string;
  room_code?: string | null;
}): string {
  return room.room_code ?? room.room_number;
}
