from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator

Subject = Literal['SQL', 'Python', 'Statistics', 'Data Science', 'Machine Learning',
                  'Deep Learning', 'MLOps', 'AI Engineering']
Level = Literal['Easy', 'Medium', 'Hard', 'Production grade']
Option = Annotated[int, Field(ge=0, le=3, strict=True)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ExamRequest(StrictModel):
    request_id: UUID
    subject: Subject
    level: Level


class GeneratedQuestion(StrictModel):
    text: str = Field(min_length=20, max_length=6000)
    topic: str = Field(min_length=1, max_length=200)
    kind: Literal['single', 'multiple']
    options: list[str] = Field(min_length=4, max_length=4)
    correct_options: list[Option] = Field(min_length=1, max_length=3)
    explanation: str = Field(min_length=10, max_length=4000)

    @model_validator(mode='after')
    def check_options(self):
        if any(not o.strip() for o in self.options) or len({o.strip().casefold() for o in self.options}) != 4:
            raise ValueError('Four distinct nonempty options required')
        if len(set(self.correct_options)) != len(self.correct_options):
            raise ValueError('Duplicate correct options')
        if (self.kind == 'single' and len(self.correct_options) != 1
                or self.kind == 'multiple' and len(self.correct_options) < 2):
            raise ValueError('Answer count does not match question kind')
        return self


class QuestionSet(StrictModel):
    questions: list[GeneratedQuestion] = Field(min_length=5, max_length=5)

    @model_validator(mode='after')
    def check_mix(self):
        if sum(q.kind == 'multiple' for q in self.questions) != 1:
            raise ValueError('Exactly one multiple-answer question required')
        if len({q.text.strip().casefold() for q in self.questions}) != 5:
            raise ValueError('Questions must be distinct')
        return self


class ReviewItem(StrictModel):
    correct_options: list[Option] = Field(min_length=1, max_length=3)
    explanation: str = Field(min_length=10)


class QuestionReview(StrictModel):
    suitable: bool
    reason: str
    questions: list[ReviewItem] = Field(min_length=5, max_length=5)


class SingleQuestion(GeneratedQuestion):
    kind: Literal['single']
    correct_options: list[Option] = Field(min_length=1, max_length=1)


class MultipleQuestion(GeneratedQuestion):
    kind: Literal['multiple']
    correct_options: list[Option] = Field(min_length=2, max_length=3)


class GenerationPlan(StrictModel):
    """Encode the required 4+1 mix in the provider schema, not just the prompt."""
    single_questions: list[SingleQuestion] = Field(min_length=4, max_length=4)
    multiple_question: MultipleQuestion

    def as_question_set(self):
        return QuestionSet(questions=[*self.single_questions, self.multiple_question])


class SingleReview(ReviewItem):
    correct_options: list[Option] = Field(min_length=1, max_length=1)


class MultipleReview(ReviewItem):
    correct_options: list[Option] = Field(min_length=2, max_length=3)


class ReviewPlan(StrictModel):
    suitable: bool
    reason: str
    single_questions: list[SingleReview] = Field(min_length=4, max_length=4)
    multiple_question: MultipleReview

    def as_review(self):
        return QuestionReview(suitable=self.suitable, reason=self.reason,
                              questions=[*self.single_questions, self.multiple_question])


class PerformanceFeedback(StrictModel):
    summary: str = Field(min_length=10)
    strengths: list[str]
    focus_areas: list[str]
    next_steps: list[str]


class AnswerInput(StrictModel):
    question_id: int = Field(gt=0, strict=True)
    selected_options: list[Option] = Field(max_length=4)

    @model_validator(mode='after')
    def unique_options(self):
        if len(set(self.selected_options)) != len(self.selected_options):
            raise ValueError('Duplicate selected options')
        return self


class AnswerBatch(StrictModel):
    answers: list[AnswerInput] = Field(min_length=1, max_length=5)

    @model_validator(mode='after')
    def unique_questions(self):
        if len({a.question_id for a in self.answers}) != len(self.answers):
            raise ValueError('Duplicate question IDs')
        return self
