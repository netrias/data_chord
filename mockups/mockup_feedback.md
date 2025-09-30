Takeaways:

Breaking things out into a step-delineated workflow with column → model mapping as its own step makes the process much more approachable. There will be a lot of minutia, but structurally the upload, column → model map, and preview steps seem roughly correct. The data work section has a good start but requires more work.



Specific Notes:

Data Upload Page:

We likely want more supporting text/direction around the data upload.

May want to use “analyze” wording around initiating the column → model mapping.

Rename the application

Column → Model Mapping Page

Be more explicit in the direction on the mapping screen

You should be able to sort the columns by different criteria

Need to make it clear that the columns are themselves in a column instead of a row

There are coloration mismatches

Make manual overrides more visually distinctive

Probably bucket AI confidence into low, medium, and high rather than showing numbers.

In the data preview, it could be nice to allow manual ads of other columns for context

Better signpost which column’s data you are previewing in the data preview

Add on hover/selection state for columns

The help text is too late

Data preview could potentially be put on the right

Add filtering--filter on confidence, on items that have been changed, on “no map” recommendations, etc.

Data Preview

If we were interested in doing a heatmap, this would be the place to do it. This is also where we’d want any other graphs or metrics.



Data Work

There was initial confusion over whether it was a row or column representation. “Batch” was also not clear.

There seemed to be general approval of the basic idea of color coding, sorting, and batching once explained--but there was also a desire that users could sort through the data in different ways. This seems desirable, but no other approaches have been modeled out at this point.

(Chris Note) There should potentially be a way to pick a column and a model manually on this page and do a run. It would be a pain to harmonize 9/10 columns, realize you missed one, and have to go through the process again. We’ll also want to think about save checkpoints, and how to reset the workflow.
